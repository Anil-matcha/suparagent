"""Lead-driven team setup: validated proposals, receipts, scoped undo and housekeeping.

Chat plans stay pending until the person approves their current version; approval
revalidates and applies the whole plan in one transaction. The request row is the
receipt: retries replay the stored state. Undo refuses when anything it touched
has changed. Plans cannot carry approval, delegation, memory, apps or archive settings.
"""
import hashlib
import json
from sqlalchemy import select, func
from app.core.config import settings
from app.db.models import Agent, AgentJob, Approval, Thread, Team, TeamEvent, TeamSetupRequest, Turn
from app.db.transactions import begin_write
from app.services import agent_memory
from app.team import fence
from app.team.service import TeamError, audit, clean_line, member, now, team_for

MAX_OPERATIONS = 8
TEXT_FIELDS = {'name': 80, 'title': 120, 'description': 200}
EDITABLE = ('name', 'title', 'description', 'instructions', 'provider', 'model')
CREATE_KEYS = set(EDITABLE) | {'op', 'share_workspace', 'first_task'}
UPDATE_KEYS = {'op', 'agent_id', 'fields', 'expected_revision'}
HASHED = EDITABLE + ('team_id', 'archived_at', 'approval_mode', 'workspace_path', 'delegation_enabled',
                    'memory_enabled', 'recall_enabled', 'memory_upkeep', 'compaction_summary', 'use_helper',
                    'fallback_provider', 'fallback_model', 'pinned')
ACTIVE_JOBS = ('queued', 'running', 'needs_input', 'waiting', 'resume')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def revision(agent):
    """Short hash of the fields a plan may edit; pins an update to what the lead saw."""
    return full_hash(agent)[:16]


def full_hash(agent):
    """Hash of everything that matters to undo: any later edit makes undo stale."""
    return digest({f: getattr(agent, f) for f in HASHED})


def snapshot(agent):
    return {f: getattr(agent, f) for f in EDITABLE}


async def engine_catalog(rt):
    return await rt.engines.describe()


async def catalog(db, owner, caller_id, *, engines):
    caller = await member(db, owner, caller_id)
    team = await team_for(db, owner)
    if not team or caller.team_id != team.id:
        raise TeamError('no_team', 'Choose a team lead first.')
    rows = (await db.execute(select(Agent).where(Agent.owner_id == owner, Agent.team_id == team.id, Agent.archived_at.is_(None)).order_by(Agent.id))).scalars()
    return {
        'team': fence.line(team.name), 'lead_agent_id': team.lead_agent_id,
        'agents': [{'id': a.id, 'name': fence.line(a.name), 'title': fence.line(a.title or ''), 'provider': a.provider, 'model': a.model,
                    'is_lead': a.id == team.lead_agent_id, 'revision': revision(a),
                    'description': fence.line(a.description or ''), 'instructions': agent_memory.redact_secrets(a.instructions or '')[0],
                    'workspace_path': a.workspace_path} for a in rows],
        'engines': engines,
        'limits': {'max_operations': MAX_OPERATIONS, 'max_created_per_turn': settings.TEAM_MAX_CREATED_PER_TURN, 'max_agents': settings.TEAM_MAX_AGENTS},
        'scope': 'You may create specialists and edit name, title, description, instructions and engine of teammates. Permissions, approvals and memory are never changed here.',
    }


async def busy(db, agent_id, *, except_thread=None):
    running = select(Turn.id).join(Thread, Thread.id == Turn.thread_id).where(Thread.agent_id == agent_id, Turn.status == 'running')
    if except_thread is not None:
        running = running.where(Thread.id != except_thread)
    if await db.scalar(running.limit(1)):
        return True
    return bool(await db.scalar(select(AgentJob.id).where(
        (AgentJob.assignee_agent_id == agent_id) | (AgentJob.requester_agent_id == agent_id), AgentJob.status.in_(ACTIVE_JOBS)).limit(1)))


def text_value(field, value):
    if field == 'instructions':
        if not isinstance(value, str) or len(value) > 2000:
            raise TeamError('invalid_input', 'Instructions must be at most 2000 characters.')
        return agent_memory.redact_secrets(value)[0]
    if field in TEXT_FIELDS:
        return clean_line(value, TEXT_FIELDS[field], field.capitalize())
    if field == 'provider':
        if not isinstance(value, str) or not value or len(value) > 40:
            raise TeamError('invalid_input', 'Engine must be an engine id.')
        return value
    if field == 'model':
        if value is not None and (not isinstance(value, str) or not value or len(value) > 120):
            raise TeamError('invalid_input', 'Model must be a model id.')
        return value
    raise TeamError('invalid_input', 'Unrecognised setting.')


async def validate(db, owner, team, caller, plan, *, turn_id, check_engine):
    """Normalise and check a plan against the current state. Raises TeamError."""
    if team is None or caller.team_id != team.id or team.lead_agent_id != caller.id:
        raise TeamError('lead_only', 'Only the team lead can change the team.')
    if not isinstance(plan, dict):
        raise TeamError('invalid_input', 'A plan must be an object.')
    operations = plan.get('operations')
    summary = plan.get('summary')
    if summary is not None and (not isinstance(summary, str) or len(summary) > 300):
        raise TeamError('invalid_input', 'A plan summary must be at most 300 characters.')
    if set(plan or ()) - {'operations', 'summary'} or not isinstance(operations, list) or not 1 <= len(operations) <= MAX_OPERATIONS:
        raise TeamError('invalid_input', f'A plan needs one to {MAX_OPERATIONS} operations.')
    live = list((await db.execute(select(Agent).where(Agent.owner_id == owner, Agent.archived_at.is_(None)).order_by(Agent.id).with_for_update().execution_options(populate_existing=True))).scalars())
    names = {a.id: a.name for a in live}
    out, touched, creating = [], set(), 0
    for raw in operations:
        if not isinstance(raw, dict):
            raise TeamError('invalid_input', 'Each operation must be an object.')
        if raw.get('op') == 'create':
            if set(raw) - CREATE_KEYS or 'name' not in raw:
                raise TeamError('invalid_input', 'Unrecognised or missing specialist settings.')
            fields = {f: text_value(f, raw[f]) for f in EDITABLE if f in raw}
            fields.setdefault('title', 'Specialist')
            fields.setdefault('instructions', '')
            fields.setdefault('provider', caller.provider)
            if 'model' not in raw:
                fields['model'] = caller.model if fields['provider'] == caller.provider else None
            check_engine(fields['provider'], fields['model'])
            if 'share_workspace' in raw and type(raw['share_workspace']) is not bool:
                raise TeamError('invalid_input', 'share_workspace must be a boolean.')
            first_task = raw.get('first_task')
            if first_task is not None:
                if (not isinstance(first_task, str) or not first_task.strip() or len(first_task) > 1000
                        or any(ord(char) < 32 and char not in '\n\t' for char in first_task)):
                    raise TeamError('invalid_input', 'A first task must be plain text of at most 1,000 characters.')
                first_task = agent_memory.redact_secrets(first_task.strip())[0]
            if raw.get('share_workspace') is True:
                if not caller.workspace_path:
                    raise TeamError('project_required', 'Give the lead a project folder first.')
                fields['workspace_path'] = caller.workspace_path
            if fields['name'] in names.values():
                raise TeamError('name_taken', 'A live agent already uses that name.')
            names[-len(out) - 1] = fields['name']
            creating += 1
            out.append({'op': 'create', 'fields': fields, 'first_task': first_task})
        elif raw.get('op') == 'update':
            if set(raw) - UPDATE_KEYS or type(raw.get('agent_id')) is not int or not isinstance(raw.get('fields'), dict) or not raw['fields']:
                raise TeamError('invalid_input', 'An update needs agent_id and fields.')
            if set(raw['fields']) - set(EDITABLE):
                raise TeamError('invalid_input', 'Those settings cannot be changed by a plan.')
            if raw['agent_id'] in touched:
                raise TeamError('invalid_input', 'Each agent may appear once per plan.')
            touched.add(raw['agent_id'])
            target = await member(db, owner, raw['agent_id'], team_id=team.id)
            fields = {f: text_value(f, v) for f, v in raw['fields'].items()}
            if target.id == caller.id and ({'provider', 'model'} & set(fields)):
                raise TeamError('invalid_input', "The lead's own engine is changed by the person.")
            expected = raw.get('expected_revision')
            if expected is not None and expected != revision(target):
                raise TeamError('changed_since', 'That agent changed since you read it. Call list_team_setup again.')
            provider = fields.get('provider', target.provider)
            model = fields['model'] if 'model' in fields else (None if 'provider' in fields and provider != target.provider else target.model)
            if {'provider', 'model'} & set(fields):
                fields['model'] = model
                check_engine(provider, model)
            if 'name' in fields and fields['name'] != target.name and fields['name'] in names.values():
                raise TeamError('name_taken', 'A live agent already uses that name.')
            names[target.id] = fields.get('name', target.name)
            if target.id != caller.id and await busy(db, target.id):
                raise TeamError('agent_busy', 'That agent is working right now. Try again when it is idle.')
            out.append({'op': 'update', 'agent_id': target.id, 'name': target.name, 'current': snapshot(target), 'fields': fields})
        else:
            raise TeamError('invalid_input', 'Operations are create or update.')
    already = await db.scalar(select(func.count()).select_from(TeamEvent).where(
        TeamEvent.team_id == team.id, TeamEvent.kind == 'agent_created', TeamEvent.data['turn_id'].as_integer() == turn_id))
    if creating and already + creating > settings.TEAM_MAX_CREATED_PER_TURN:
        raise TeamError('creation_limit', 'This turn reached its specialist creation limit.')
    if len(live) + creating > settings.TEAM_MAX_AGENTS:
        raise TeamError('agent_limit', 'The live agent limit has been reached.')
    return out


def redacted(value):
    if isinstance(value, str):
        return agent_memory.redact_secrets(value)[0]
    if isinstance(value, dict):
        return {key: redacted(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redacted(item) for item in value]
    return value


def changes(before, after):
    def safe(value):
        return agent_memory.redact_secrets(value)[0] if isinstance(value, str) else value
    return [{'field': key, 'before': safe(before.get(key)), 'after': safe(value)}
            for key, value in after.items() if key not in before or before[key] != value]


async def undo_status(db, row):
    if row.kind == 'routine':
        from app.team import routine_changes
        return await routine_changes.status(db, row)
    if row.kind != 'setup' or row.state != 'applied':
        return {'available': False, 'reason': 'This change is no longer undoable.'}
    team = await team_for(db, row.owner_id)
    if team is None or team.id != row.team_id:
        return {'available': False, 'reason': 'The team changed.'}
    for kind in ('created', 'updated'):
        for entry in (row.undo or {}).get(kind, []):
            agent = await db.get(Agent, entry['agent_id'])
            if agent is None or agent.archived_at is not None or full_hash(agent) != entry['hash']:
                return {'available': False, 'reason': 'An agent changed after this setup.'}
            if kind == 'created':
                if agent.id == team.lead_agent_id:
                    return {'available': False, 'reason': 'This agent is now the team lead.'}
                used = await db.scalar(select(Thread.id).where(Thread.agent_id == agent.id).limit(1))
                used = used or await db.scalar(select(AgentJob.id).where((AgentJob.assignee_agent_id == agent.id) | (AgentJob.requester_agent_id == agent.id)).limit(1))
                if used:
                    return {'available': False, 'reason': 'A created agent has already been used.'}
            if await busy(db, agent.id):
                return {'available': False, 'reason': 'An agent is working right now.'}
    return {'available': True, 'reason': None}


def replay(row):
    return {**row.result, 'request_id': row.request_id, 'state': row.state, 'replayed': True}


async def existing(db, owner, request_id):
    return await db.scalar(select(TeamSetupRequest).where(TeamSetupRequest.owner_id == owner, TeamSetupRequest.request_id == request_id).execution_options(populate_existing=True))


def public_plan(plan):
    """Return only the user-editable plan, without server-side replay metadata."""
    return {key: value for key, value in (plan or {}).items() if not key.startswith('_')}


def executable_plan(plan):
    """Strip display-only target snapshots from the plan before validation or application."""
    value = redacted(plan)
    for operation in value.get('operations', []) if isinstance(value, dict) else []:
        if isinstance(operation, dict) and operation.get('op') == 'update':
            operation.pop('name', None)
            operation.pop('current', None)
    return value


def request_version(row):
    return int((row.result or {}).get('version', (row.plan or {}).get('_version', 1)))


def review_plan(plan, operations):
    """Show resolved defaults while preserving the revisions the person is approving."""
    reviewed = []
    raw_operations = plan.get('operations', []) if isinstance(plan, dict) else []
    for raw, operation in zip(raw_operations, operations, strict=True):
        if operation['op'] == 'create':
            fields = operation['fields']
            reviewed.append({'op': 'create', **{field: fields[field] for field in EDITABLE if field in fields},
                             'share_workspace': raw.get('share_workspace') is True,
                             'first_task': operation.get('first_task') or ''})
        else:
            reviewed.append({'op': 'update', 'agent_id': operation['agent_id'], 'fields': operation['fields'],
                             'name': operation.get('name', f"Agent {operation['agent_id']}"),
                             'current': redacted(operation.get('current', {})), 'expected_revision': raw['expected_revision']})
    return {**({'summary': plan['summary']} if isinstance(plan, dict) and 'summary' in plan else {}), 'operations': reviewed}


def _guided_plan(plan):
    for operation in plan.get('operations', []) if isinstance(plan, dict) else []:
        if not isinstance(operation, dict):
            continue
        if operation.get('op') == 'create':
            if not isinstance(operation.get('instructions'), str) or not operation['instructions'].strip():
                raise TeamError('instructions_required', "Describe this specialist's purpose and expected output in its instructions.")
            if not isinstance(operation.get('first_task'), str) or not operation['first_task'].strip():
                raise TeamError('first_task_required', 'Add a useful, editable first task for each new specialist.')
        if operation.get('op') == 'update' and not operation.get('expected_revision'):
            raise TeamError('revision_required', 'Read list_team_setup and include expected_revision before editing an agent.')


async def _apply_operations(db, owner, caller, team, turn_id, thread_id, operations, check_engine):
    """Apply already-validated operations and return the receipt and safe Undo data."""
    results, undo = [], {'created': [], 'updated': []}
    for op in operations:
        if op['op'] == 'create':
            fields = dict(op['fields'])
            agent = Agent(owner_id=owner, team_id=team.id, created_by_agent_id=caller.id, approval_mode='ask', **fields)
            db.add(agent)
            await db.flush()
            audit(db, team, 'agent_created', agent_id=agent.id, thread_id=thread_id, data={'turn_id': turn_id, 'instructions_length': len(agent.instructions or '')})
            result = {'action': 'created', 'agent_id': agent.id, 'name': agent.name,
                      'changes': changes({}, {**snapshot(agent), 'workspace_path': agent.workspace_path, 'approval_mode': 'ask'})}
            if op.get('first_task'):
                result['first_task'] = op['first_task']
            results.append(result)
            undo['created'].append({'agent_id': agent.id, 'hash': None})
        else:
            agent = await db.get(Agent, op['agent_id'], with_for_update=True)
            before = snapshot(agent)
            for key, value in op['fields'].items():
                setattr(agent, key, value)
            audit(db, team, 'agent_updated', agent_id=agent.id, thread_id=thread_id, data={'turn_id': turn_id, 'fields': sorted(op['fields'])})
            results.append({'action': 'updated', 'agent_id': agent.id, 'name': agent.name,
                            'changes': changes(before, {key: getattr(agent, key) for key in op['fields']})})
            undo['updated'].append({'agent_id': agent.id, 'before': before, 'hash': None})
    await db.flush()
    for entry in undo['created']:
        entry['hash'] = full_hash(await db.get(Agent, entry['agent_id']))
    for entry in undo['updated']:
        entry['hash'] = full_hash(await db.get(Agent, entry['agent_id']))
    for result_row in results:
        saved = await db.get(Agent, result_row['agent_id'])
        check_engine(saved.provider, saved.model)
    return results, undo


async def prepare_pending(db, owner, caller_id, turn_id, thread_id, plan, *, check_engine, request_id=None, authorize=None):
    """Validate a proposed setup and persist it for the person to review."""
    await begin_write(db)
    team = await team_for(db, owner, lock=True)
    caller = await db.get(Agent, caller_id, with_for_update=True, populate_existing=True)
    if caller is None or caller.owner_id != owner or caller.archived_at is not None or team is None or caller.team_id != team.id or team.lead_agent_id != caller_id:
        raise TeamError('lead_only', 'Only the current team lead can change the team.')
    if not thread_id:
        raise TeamError('invalid_input', 'A reviewable plan needs its originating conversation.')
    if authorize:
        await authorize(db)
    safe_plan = redacted(plan)
    source_digest = digest(safe_plan)
    request_id = request_id or digest([turn_id, safe_plan])[:32]
    if not isinstance(request_id, str) or not 0 < len(request_id) <= 64:
        raise TeamError('invalid_input', 'request_id must be at most 64 characters.')
    row = await existing(db, owner, request_id)
    if row is not None:
        saved_digest = (row.plan or {}).get('_request_digest', digest(public_plan(row.plan)))
        if row.kind != 'setup' or row.lead_agent_id != caller_id or row.thread_id != thread_id or saved_digest != source_digest:
            raise TeamError('request_conflict', 'This request_id belongs to a different plan or conversation. Use a new request_id for a changed plan.')
        replayed = {**(row.result or {}), 'request_id': row.request_id, 'state': row.state, 'version': request_version(row), 'plan': public_plan(row.plan), 'replayed': True}
        if row.state == 'pending':
            from app.services import thread_store
            event = await thread_store.add_event_in_transaction(db, thread_id, 'team.setup.proposed',
                                                                {'request_id': row.request_id, 'version': request_version(row), 'plan': public_plan(row.plan)},
                                                                turn_id=turn_id)
            await db.commit()
            replayed['notify_thread_id'] = thread_id
            replayed['event_seq'] = event.seq
        else:
            await db.rollback()
        return replayed
    _guided_plan(plan)
    operations = await validate(db, owner, team, caller, plan, turn_id=turn_id, check_engine=check_engine)
    card_plan = review_plan(safe_plan, operations)
    if authorize:
        await authorize(db)
    row = TeamSetupRequest(owner_id=owner, team_id=team.id, lead_agent_id=caller.id, thread_id=thread_id, turn_id=turn_id,
                           request_id=request_id, kind='setup', plan={**card_plan, '_request_digest': source_digest, '_version': 1},
                           state='pending', result={'version': 1}, undo={})
    db.add(row)
    await db.flush()
    from app.services import thread_store
    event = await thread_store.add_event_in_transaction(db, thread_id, 'team.setup.proposed',
                                                        {'request_id': request_id, 'version': 1, 'plan': card_plan}, turn_id=turn_id)
    await db.commit()
    return {'request_id': request_id, 'state': 'pending', 'version': 1, 'plan': card_plan, 'replayed': False,
            'notify_thread_id': thread_id, 'event_seq': event.seq}


async def revise_pending(db, owner, request_id, version, plan, *, check_engine):
    """Replace a pending plan only when the caller edits the version they saw."""
    await begin_write(db)
    team = await team_for(db, owner, lock=True)
    row = await db.scalar(select(TeamSetupRequest).where(TeamSetupRequest.owner_id == owner, TeamSetupRequest.request_id == request_id)
                          .with_for_update().execution_options(populate_existing=True))
    if row is None or row.kind != 'setup' or row.state != 'pending' or team is None or team.id != row.team_id:
        raise TeamError('request_not_found', 'This team setup plan is no longer waiting for review.')
    caller = await db.get(Agent, row.lead_agent_id, with_for_update=True, populate_existing=True)
    if caller is None or caller.owner_id != owner or caller.archived_at is not None or caller.team_id != team.id or team.lead_agent_id != caller.id:
        raise TeamError('lead_only', 'The team lead changed. Refresh the team before reviewing this plan.')
    thread = await db.get(Thread, row.thread_id, with_for_update=True, populate_existing=True)
    if thread is None or thread.owner_id != owner or thread.agent_id != caller.id:
        raise TeamError('request_not_found', 'The conversation for this setup plan no longer exists.')
    current_version = request_version(row)
    if version != current_version:
        await db.rollback()
        raise TeamError('changed_since', 'This plan changed in another view. Refresh it before saving.')
    safe_plan = redacted(plan)
    executable = executable_plan(safe_plan)
    _guided_plan(executable)
    operations = await validate(db, owner, team, caller, executable, turn_id=row.turn_id, check_engine=check_engine)
    card_plan = review_plan(safe_plan, operations)
    next_version = current_version + 1
    row.plan = {**card_plan, '_request_digest': (row.plan or {}).get('_request_digest', digest(public_plan(row.plan))), '_version': next_version}
    row.result = {'version': next_version}
    from app.services import thread_store
    await thread_store.add_event_in_transaction(db, row.thread_id, 'team.setup.proposed',
                                                {'request_id': row.request_id, 'version': next_version, 'plan': card_plan})
    await db.commit()
    return {'request_id': row.request_id, 'state': 'pending', 'version': next_version, 'plan': card_plan, 'thread_id': row.thread_id}


async def decide_pending(db, owner, request_id, version, *, approve, check_engine):
    """Approve the exact pending version, rechecking current team state under lock, or decline it."""
    await begin_write(db)
    team = await team_for(db, owner, lock=True)
    row = await db.scalar(select(TeamSetupRequest).where(TeamSetupRequest.owner_id == owner, TeamSetupRequest.request_id == request_id)
                          .with_for_update().execution_options(populate_existing=True))
    if row is None or row.kind != 'setup' or row.state != 'pending' or team is None or team.id != row.team_id:
        raise TeamError('request_not_found', 'This team setup plan is no longer waiting for review.')
    caller = await db.get(Agent, row.lead_agent_id, with_for_update=True, populate_existing=True)
    if caller is None or caller.owner_id != owner or caller.archived_at is not None or caller.team_id != team.id or team.lead_agent_id != caller.id:
        raise TeamError('lead_only', 'The team lead changed. This plan was left unapplied.')
    current_version = request_version(row)
    if version != current_version:
        await db.rollback()
        raise TeamError('changed_since', 'This plan changed in another view. Refresh it before deciding.')
    thread = await db.get(Thread, row.thread_id, with_for_update=True, populate_existing=True)
    if thread is None or thread.owner_id != owner or thread.agent_id != caller.id:
        raise TeamError('request_not_found', 'The conversation for this setup plan no longer exists.')
    if approve:
        plan = executable_plan(public_plan(row.plan))
        _guided_plan(plan)
        operations = await validate(db, owner, team, caller, plan, turn_id=row.turn_id, check_engine=check_engine)
        results, undo_data = await _apply_operations(db, owner, caller, team, row.turn_id, row.thread_id, operations, check_engine)
        result = {'results': results, 'model_note': 'New conversations use the saved model defaults. Existing conversations retain their model.',
                  'capability_note': 'App connections, skills, computers and routines require their own setup.'}
        row.state, row.result, row.undo, row.decided_at = 'applied', result, undo_data, now()
        event_data = {'request_id': row.request_id, 'results': results, 'undo_available': True}
        event_type = 'team.setup.applied'
    else:
        row.state, row.result, row.decided_at = 'rejected', {'version': current_version}, now()
        event_data = {'request_id': row.request_id, 'version': current_version, 'state': 'rejected'}
        event_type = 'team.setup.dismissed'
    from app.services import thread_store
    await thread_store.add_event_in_transaction(db, row.thread_id, event_type, event_data)
    await db.commit()
    return {'request_id': row.request_id, 'state': row.state, 'thread_id': row.thread_id}


async def propose(db, owner, caller_id, turn_id, thread_id, plan, *, check_engine, request_id=None, authorize=None, guided=False):
    await begin_write(db)
    team = await team_for(db, owner, lock=True)
    caller = await db.get(Agent, caller_id, with_for_update=True, populate_existing=True)
    if caller is None or caller.owner_id != owner or caller.archived_at is not None or team is None or caller.team_id != team.id or team.lead_agent_id != caller_id:
        raise TeamError('lead_only', 'Only the current team lead can change the team.')
    if authorize:
        await authorize(db)
    request_id = request_id or digest([turn_id, plan])[:32]
    if not isinstance(request_id, str) or not 0 < len(request_id) <= 64:
        raise TeamError('invalid_input', 'request_id must be at most 64 characters.')
    row = await existing(db, owner, request_id)
    if row is not None:
        if row.kind != 'setup' or row.lead_agent_id != caller_id or row.thread_id != thread_id or row.plan.get('_request_digest', digest(row.plan)) != digest(plan):
            raise TeamError('request_conflict', 'This request_id belongs to a different plan or conversation. Use a new request_id for a changed plan.')
        found = replay(row)
        await db.rollback()
        return found
    if guided:
        for operation in plan.get('operations', []) if isinstance(plan, dict) else []:
            if not isinstance(operation, dict):
                continue
            if operation.get('op') == 'create' and (not isinstance(operation.get('instructions'), str) or not operation['instructions'].strip()):
                raise TeamError('instructions_required', "Describe this specialist's purpose and expected output in its instructions.")
            if operation.get('op') == 'update' and not operation.get('expected_revision'):
                raise TeamError('revision_required', 'Read list_team_setup and include expected_revision before editing an agent.')
    operations = await validate(db, owner, team, caller, plan, turn_id=turn_id, check_engine=check_engine)
    results, undo = [], {'created': [], 'updated': []}
    for op in operations:
        if op['op'] == 'create':
            fields = dict(op['fields'])
            agent = Agent(owner_id=owner, team_id=team.id, created_by_agent_id=caller.id, approval_mode='ask', **fields)
            db.add(agent)
            await db.flush()
            audit(db, team, 'agent_created', agent_id=agent.id, thread_id=thread_id, data={'turn_id': turn_id, 'instructions_length': len(agent.instructions or '')})
            results.append({'action': 'created', 'agent_id': agent.id, 'name': agent.name,
                            'changes': changes({}, {**snapshot(agent), 'workspace_path': agent.workspace_path, 'approval_mode': 'ask'})})
            undo['created'].append({'agent_id': agent.id, 'hash': None})
        else:
            agent = await db.get(Agent, op['agent_id'], with_for_update=True)
            before = snapshot(agent)
            for key, value in op['fields'].items():
                setattr(agent, key, value)
            audit(db, team, 'agent_updated', agent_id=agent.id, thread_id=thread_id, data={'turn_id': turn_id, 'fields': sorted(op['fields'])})
            results.append({'action': 'updated', 'agent_id': agent.id, 'name': agent.name,
                            'changes': changes(before, {key: getattr(agent, key) for key in op['fields']})})
            undo['updated'].append({'agent_id': agent.id, 'before': before, 'hash': None})
    await db.flush()
    for entry in undo['created']:
        entry['hash'] = full_hash(await db.get(Agent, entry['agent_id']))
    for entry in undo['updated']:
        entry['hash'] = full_hash(await db.get(Agent, entry['agent_id']))
    for result_row in results:
        saved = await db.get(Agent, result_row['agent_id'])
        check_engine(saved.provider, saved.model)
    if authorize:
        await authorize(db)
    result = {'results': results, 'model_note': 'New conversations use the saved model defaults. Existing conversations retain their model.',
              'capability_note': 'App connections, skills, computers and routines require their own setup.'}
    db.add(TeamSetupRequest(owner_id=owner, team_id=team.id, lead_agent_id=caller.id, thread_id=thread_id, turn_id=turn_id,
                            request_id=request_id, kind='setup', plan={**redacted(plan), '_request_digest': digest(plan)}, state='applied', result=result, undo=undo, decided_at=now()))
    await db.commit()
    return {**result, 'request_id': request_id, 'state': 'applied', 'replayed': False}


async def undo(db, owner, request_id):
    await begin_write(db)
    team = await team_for(db, owner, lock=True)
    row = await existing(db, owner, request_id)
    if row is None or team is None or row.team_id != team.id or row.kind not in ('setup', 'routine'):
        await db.rollback()
        raise TeamError('request_not_found', 'No such team change to undo.')
    if row.state != 'applied':
        code = 'already_undone' if row.state == 'undone' else 'not_undoable'
        await db.rollback()
        raise TeamError(code, 'This change cannot be undone.')
    if row.kind == 'routine':
        from app.team import routine_changes
        from app.services.routine_service import InvalidRoutine
        try:
            return await routine_changes.undo(db, owner, row, team)
        except InvalidRoutine as exc:
            await db.rollback()
            raise TeamError('changed_since', f'The saved schedule cannot be restored: {exc}') from None
    created = []
    for entry in row.undo['created']:
        agent = await db.get(Agent, entry['agent_id'], with_for_update=True)
        if agent is None or agent.archived_at is not None or full_hash(agent) != entry['hash']:
            await db.rollback()
            raise TeamError('changed_since', 'Something changed since this was applied, so it was left alone.')
        if agent.id == team.lead_agent_id:
            await db.rollback()
            raise TeamError('changed_since', 'This agent is now the team lead, so it was kept.')
        used = await db.scalar(select(Thread.id).where(Thread.agent_id == agent.id).limit(1))
        used = used or await db.scalar(select(AgentJob.id).where((AgentJob.assignee_agent_id == agent.id) | (AgentJob.requester_agent_id == agent.id)).limit(1))
        if used:
            await db.rollback()
            raise TeamError('agent_used', 'A specialist this created has already been used, so it was kept.')
        created.append(agent)
    updated = []
    for entry in row.undo['updated']:
        agent = await db.get(Agent, entry['agent_id'], with_for_update=True)
        if agent is None or agent.archived_at is not None or full_hash(agent) != entry['hash']:
            await db.rollback()
            raise TeamError('changed_since', 'Something changed since this was applied, so it was left alone.')
        if await busy(db, agent.id):
            await db.rollback()
            raise TeamError('agent_busy', 'That agent is working right now.')
        updated.append((agent, entry['before']))
    for agent in created:
        await db.delete(agent)
        audit(db, team, 'agent_create_undone', agent_id=agent.id, data={'removed': 'deleted', 'request_id': request_id})
    for agent, before in updated:
        for key, value in before.items():
            setattr(agent, key, value)
        audit(db, team, 'agent_update_undone', agent_id=agent.id, data={'request_id': request_id})
    row.state, row.decided_at = 'undone', now()
    await db.commit()
    return {'request_id': request_id, 'state': 'undone'}


async def prepare_deletion(db, owner, caller_id, turn_id, thread_id, args, *, request_id=None):
    """Validate a deletion; returns the record to carry out. The caller removes the
    agent with the shared teardown and then calls `record_deletion`."""
    caller = await member(db, owner, caller_id)
    team = await team_for(db, owner)
    if team is None or team.lead_agent_id != caller.id:
        raise TeamError('lead_only', 'Only the team lead can propose deleting an agent.')
    if set(args) - {'agent_id', 'reason'} or type(args.get('agent_id')) is not int:
        raise TeamError('invalid_input', 'Supply agent_id and a reason.')
    reason = clean_line(args.get('reason', ''), 300, 'Reason')
    target = await member(db, owner, args['agent_id'], team_id=team.id)
    if target.id == team.lead_agent_id:
        raise TeamError('lead_required', 'The lead cannot be deleted. Choose another lead first.')
    if await busy(db, target.id):
        raise TeamError('agent_busy', 'That agent is working right now. Try again when it is idle.')
    return {'agent_id': target.id, 'name': target.name, 'reason': reason, 'team_id': team.id, 'lead_agent_id': caller.id,
            'request_id': request_id or digest([turn_id, 'delete', target.id])[:32], 'turn_id': turn_id, 'thread_id': thread_id}


async def record_deletion(db, owner, record):
    await begin_write(db)
    db.add(TeamSetupRequest(owner_id=owner, team_id=record['team_id'], lead_agent_id=record['lead_agent_id'], thread_id=record['thread_id'],
                            turn_id=record['turn_id'], request_id=record['request_id'], kind='deletion',
                            plan={'agent_id': record['agent_id'], 'name': record['name'], 'reason': record['reason']},
                            state='applied', result={'results': [{'action': 'deleted', 'agent_id': record['agent_id'], 'name': record['name']}]}, decided_at=now()))
    team = await db.get(Team, record['team_id'])
    audit(db, team, 'agent_deleted', agent_id=record['agent_id'], thread_id=record['thread_id'], data={'turn_id': record['turn_id'], 'reason': record['reason']})
    await db.commit()


# --- housekeeping -------------------------------------------------------------------


async def thread_state(db, thread):
    if thread.archived_at is not None:
        return 'closed'
    if await db.scalar(select(Approval.id).where(Approval.thread_id == thread.id, Approval.status == 'pending').limit(1)):
        return 'waiting_on_person'
    if await db.scalar(select(Turn.id).where(Turn.thread_id == thread.id, Turn.status == 'running').limit(1)):
        return 'running'
    return 'idle'


async def opened_jobs(db, owner, team, caller):
    """Job conversations a teammate opened: all of them for the lead, the
    caller's own for anyone else. Newest first."""
    query = select(AgentJob).where(AgentJob.owner_id == owner, AgentJob.assignee_thread_id.is_not(None))
    if team.lead_agent_id != caller.id:
        query = query.where(AgentJob.requester_agent_id == caller.id)
    return list((await db.execute(query.order_by(AgentJob.id.desc()).limit(100))).scalars())


async def list_threads(db, owner, caller_id):
    caller = await member(db, owner, caller_id)
    team = await team_for(db, owner)
    if team is None or caller.team_id != team.id:
        raise TeamError('no_team', 'This agent has no team.')
    rows = []
    for job in await opened_jobs(db, owner, team, caller):
        thread = await db.get(Thread, job.assignee_thread_id)
        if thread is None or thread.owner_id != owner:
            continue
        opener, assignee = await db.get(Agent, job.requester_agent_id), await db.get(Agent, job.assignee_agent_id)
        rows.append({'thread_id': thread.id, 'title': fence.line(thread.title or f'Job {job.id}'), 'agent': fence.line(assignee.name) if assignee else None,
                     'opened_by': fence.line(opener.name) if opener else None, 'job_id': job.id, 'job_status': job.status, 'state': await thread_state(db, thread)})
    return rows


async def close_thread(db, owner, caller_id, thread_id, *, current_thread_id):
    await begin_write(db)
    caller = await member(db, owner, caller_id)
    team = await team_for(db, owner, lock=True)
    if team is None or caller.team_id != team.id:
        raise TeamError('no_team', 'This agent has no team.')
    if type(thread_id) is not int:
        raise TeamError('invalid_input', 'Supply a thread id from list_threads.')
    job = next((j for j in await opened_jobs(db, owner, team, caller) if j.assignee_thread_id == thread_id), None)
    thread = await db.get(Thread, thread_id)
    if job is None or thread is None or thread.owner_id != owner:
        await db.rollback()
        raise TeamError('not_allowed', 'Only the lead, or the agent that opened a thread, can close it. Call list_threads.')
    if thread_id == current_thread_id:
        await db.rollback()
        raise TeamError('current_thread', 'You cannot close the conversation you are in.')
    state = await thread_state(db, thread)
    if state == 'closed':
        await db.rollback()
        return {'thread_id': thread_id, 'state': 'closed', 'already_closed': True}
    if state == 'waiting_on_person':
        await db.rollback()
        raise TeamError('awaiting_person', 'That thread is waiting on the person. Leave it open.')
    if state == 'running' or job.status in ACTIVE_JOBS:
        await db.rollback()
        raise TeamError('thread_busy', 'That thread is still working. Wait for it, or the person can stop it.')
    thread.archived_at = now()
    audit(db, team, 'thread_closed', agent_id=caller.id, thread_id=thread_id, job_id=job.id, data={'closed_by_agent_id': caller.id})
    await db.commit()
    return {'thread_id': thread_id, 'state': 'closed', 'already_closed': False}


# --- tool dispatch (called by the team bridge) ----------------------------------------

TOOLS = ('create_agent', 'list_team_setup', 'suggest_workers', 'propose_team_setup', 'propose_bot_deletion', 'list_threads', 'close_thread')
WRITES = ('create_agent', 'propose_team_setup', 'propose_bot_deletion')


async def call(rt, run, name, args):
    """Run one setup or housekeeping tool for the turn in `run`."""
    from app.services import agent_store
    from app.services.agent_removal import remove_agent
    from app.team import policy
    if name in WRITES and (run.ctx.approval_mode == 'plan' or run.ctx.team.origin in policy.UNATTENDED):
        raise TeamError('forbidden', 'Changing the team is unavailable for this turn.')
    if name == 'suggest_workers':
        from app.services import worker_suggestions
        result = suggest_workers(args)
        # Remember the answer so the Choose your team screen can offer "Use these suggestions". Ids only: nothing is created.
        worker_suggestions.record(result['suggestions'], thread_id=run.thread_id)
        return result
    owner, caller = rt.owner_id, run.agent_id
    async with rt.session() as db:
        if name == 'list_team_setup':
            return await catalog(db, owner, caller, engines=await engine_catalog(rt))
        if name == 'list_threads':
            return {'threads': await list_threads(db, owner, caller)}
        if name == 'close_thread':
            return await close_thread(db, owner, caller, args.get('thread_id'), current_thread_id=run.thread_id)
        if name in ('create_agent', 'propose_team_setup'):
            if name == 'create_agent':
                if set(args) - {'name', 'title', 'instructions', 'share_workspace', 'first_task', 'request_id'}:
                    raise TeamError('invalid_input', 'Unrecognised specialist settings.')
                plan = {'operations': [{'op': 'create', **{key: value for key, value in args.items() if key != 'request_id'}}]}
            else:
                plan = {k: v for k, v in args.items() if k != 'request_id'}
            result = await prepare_pending(db, owner, caller, run.turn_id, run.thread_id, plan, check_engine=_checker(rt),
                                           request_id=args.get('request_id'), authorize=_authorizer(rt, run))
            if result['state'] == 'pending' and result.get('notify_thread_id'):
                event_seq = result.get('event_seq')
                if isinstance(event_seq, int):
                    run.recorder.note_stored_event(event_seq)
                else:
                    rt.hub.notify(result['notify_thread_id'])
            result.pop('notify_thread_id', None)
            result.pop('event_seq', None)
            if name == 'create_agent':
                result = {**result, 'approval_mode': 'ask'}
            return result
        record = await prepare_deletion(db, owner, caller, run.turn_id, run.thread_id, {k: v for k, v in args.items() if k != 'request_id'}, request_id=args.get('request_id'))
        previous = await existing(db, owner, record['request_id'])
        if previous is not None:
            return replay(previous)
    try:
        await remove_agent(rt, record['agent_id'])
    except agent_store.AgentNotFound:
        raise TeamError('agent_not_found', 'No live agent with that id.') from None
    async with rt.session() as db:
        await record_deletion(db, owner, record)
    result = {'results': [{'action': 'deleted', 'agent_id': record['agent_id'], 'name': record['name']}], 'request_id': record['request_id'], 'state': 'applied', 'replayed': False}
    await run.recorder.emit('team.setup.applied', {'request_id': record['request_id'], 'results': result['results'], 'undo_available': False})
    return result


def _authorizer(rt, run):
    async def authorize(db):
        from app.core import app_settings
        from app.team import policy
        turn = await db.get(Turn, run.turn_id, with_for_update=True, populate_existing=True)
        thread = await db.get(Thread, run.thread_id)
        if (run.ctx.aborted or not app_settings.get('TEAM_ENABLED') or turn is None or turn.status != 'running'
                or turn.owner_token != run.owner_token or turn.origin in policy.UNATTENDED
                or thread is None or thread.owner_id != rt.owner_id or thread.agent_id != run.agent_id
                or run.ctx.approval_mode == 'plan' or run.ctx.team.origin in policy.UNATTENDED
                or (not run.ctx.team.configuration_allowed and not run.ctx.team.setup_allowed)):
            raise TeamError('forbidden', 'This turn no longer has permission to change the team.')
    return authorize


def _checker(rt):
    from app.services.thread_runtime import EngineModelInvalid, UnknownProvider

    def check(provider, model):
        try:
            rt.check_engine_model(provider, model)
        except UnknownProvider:
            raise TeamError('unknown_provider', f'Unknown engine: {provider}') from None
        except EngineModelInvalid as exc:
            raise TeamError('unknown_model', str(exc)) from None
    return check


def suggest_workers(args):
    """Rank bundled workers for stated goals. Read-only: catalog data only, no database, model or network use."""
    from app.templates import suggest
    if set(args) - {'goals', 'about', 'max_setup', 'request_id'}:
        raise TeamError('invalid_input', 'Unrecognised suggestion settings.')
    goals = args.get('goals') or []
    if not isinstance(goals, list) or len(goals) > 8 or not all(isinstance(g, str) and len(g) <= 120 for g in goals):
        raise TeamError('invalid_input', 'Give up to eight goals of 120 characters or fewer.')
    about = args.get('about') or ''
    if not isinstance(about, str) or len(about) > 500:
        raise TeamError('invalid_input', 'Describe the business in 500 characters or fewer.')
    max_setup = args.get('max_setup') or 'guided'
    if max_setup not in suggest.SETUP_RANK:
        raise TeamError('invalid_input', 'Use express, standard or guided.')
    found = suggest.suggest(goals, about, max_setup=max_setup)
    return {'suggestions': [{'template_id': f.template_id, 'reason': f.reason} for f in found]}
