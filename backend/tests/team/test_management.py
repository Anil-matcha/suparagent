"""Configuration tools reuse persisted settings, revisions and turn authority."""
import pytest
from sqlalchemy import select
from app.db.models import Agent, Routine, TeamSetupRequest
from app.services.provider_adapter import TurnOutcome
from app.team import management, policy, setup, service
from tests.team.test_collaboration import harness  # noqa: F401
from tests.services.thread_harness import sessions, owner, cleanup, wait_until  # noqa: F401


async def run_tools(harness, operation):
    rt, engine, lead, worker = harness
    thread = await rt.create_thread(agent_id=lead.id)
    seen = {}
    async def handler(ctx, sink, gate):
        seen.update(await operation(rt, rt._runs[ctx.turn_id], worker))
        return TurnOutcome('succeeded', result='done')
    engine.handlers[thread.id] = handler
    turn = await rt.send_turn(thread.id, 'Configure my agent')
    await wait_until(lambda: turn.id not in rt._runs)
    return seen


async def test_create_preview_resume_and_retry_one_routine(harness, sessions, owner):
    async def operation(rt, run, worker):
        preview = await management.call(rt, run, 'preview_schedule', {'schedule_kind': 'weekdays', 'schedule_spec': {'days': [1,2,3,4,5], 'time': '09:00'}, 'timezone': 'Asia/Kolkata'})
        args = {'agent_id': worker.id, 'name': 'Daily file brief', 'prompt': 'Summarize the files.', **{key: preview[key] for key in ('schedule_kind', 'schedule_spec', 'timezone')}, 'request_id': 'daily'}
        made = await management.call(rt, run, 'create_routine', args)
        again = await management.call(rt, run, 'create_routine', args)
        revision = made['routine']['revision']
        resumed = await management.call(rt, run, 'manage_routine', {'agent_id': worker.id, 'routine_id': made['routine']['id'], 'action': 'resume', 'expected_revision': revision})
        return {'made': made, 'again': again, 'resumed': resumed}
    seen = await run_tools(harness, operation)
    assert seen['made']['routine']['paused'] is True
    assert seen['made']['routine']['next_dates']
    assert seen['again']['replayed'] is True
    assert seen['resumed']['routine']['paused'] is False
    async with sessions() as db:
        rows = list((await db.execute(select(Routine).where(Routine.owner_id == owner))).scalars())
        assert len(rows) == 1 and rows[0].approval_mode == 'ask' and rows[0].next_run_at is not None


async def test_profile_edits_same_agent_and_undo_preserves_permissions(harness, sessions, owner):
    async def operation(rt, run, worker):
        async with sessions() as db:
            revision = setup.revision(await db.get(Agent, worker.id))
        result = await management.call(rt, run, 'propose_profile', {'agent_id': worker.id, 'expected_revision': revision, 'fields': {'instructions': 'Summarize Product Hunt launches.'}})
        async with sessions() as db:
            worker = await db.get(Agent, worker.id)
            assert worker.instructions == 'Summarize Product Hunt launches.' and worker.approval_mode == 'ask'
        return result
    seen = await run_tools(harness, operation)
    assert seen['state'] == 'applied' and seen['results'][0]['changes'][0]['field'] == 'instructions'
    async with sessions() as db:
        assert (await setup.undo(db, owner, seen['request_id']))['state'] == 'undone'


async def test_nonlead_cannot_manage_another_agent(harness, sessions, owner):
    rt, engine, lead, worker = harness
    thread = await rt.create_thread(agent_id=worker.id)
    seen = []
    async def handler(ctx, sink, gate):
        for tool, args in [('list_routines', {'agent_id': lead.id}), ('skills_list', {'agent_id': lead.id})]:
            with pytest.raises(service.TeamError) as exc:
                await management.call(rt, rt._runs[ctx.turn_id], tool, args)
            seen.append(exc.value.code)
        return TurnOutcome('succeeded')
    engine.handlers[thread.id] = handler
    turn = await rt.send_turn(thread.id, 'Read lead config')
    await wait_until(lambda: turn.id not in rt._runs)
    assert seen == ['lead_only', 'lead_only']


@pytest.mark.parametrize('tool', management.WRITES)
@pytest.mark.parametrize('origin,mode', [('outside','ask'), ('webhook','ask'), ('routine','ask'), ('harness','ask'), ('person','plan')])
def test_management_mutation_floor(tool, origin, mode):
    decision = policy.automatic('mcp__team__' + tool, mode, policy.TurnTeam(1, 1, True, True, origin=origin), {})
    assert decision[0] == 'denied'


async def test_restricted_chat_scope_stays_restricted_in_job_origin(harness, sessions, owner):
    rt, engine, lead, worker = harness
    thread = await rt.create_thread(agent_id=lead.id)
    seen = []
    async def handler(ctx, sink, gate):
        assert ctx.team.origin == 'restricted' and ctx.team.configuration_allowed is False
        with pytest.raises(service.TeamError) as exc:
            await management.call(rt, rt._runs[ctx.turn_id], 'create_routine', {'name': 'No', 'prompt': 'No'})
        seen.append(exc.value.code)
        return TurnOutcome('succeeded')
    engine.handlers[thread.id] = handler
    turn = await rt.send_turn(thread.id, 'Configure something', configuration_allowed=False)
    await wait_until(lambda: turn.id not in rt._runs)
    assert seen == ['forbidden']


async def test_skill_authoring_is_disabled_and_scoped(harness, sessions, owner, monkeypatch, tmp_path):
    from app.services import agent_memory, agent_skills
    monkeypatch.setattr(agent_memory, 'agent_home', lambda agent_id: tmp_path / str(agent_id))
    monkeypatch.setattr(agent_skills, 'agent_home', lambda agent_id: tmp_path / str(agent_id))
    async def operation(rt, run, worker):
        args = {'agent_id': worker.id, 'action': 'create', 'name': 'file-brief', 'description': 'Summarize local files', 'body': 'Read only the provided files and cite each finding.', 'request_id': 'skill-once'}
        saved = await management.call(rt, run, 'skill_manage', args)
        replay = await management.call(rt, run, 'skill_manage', args)
        listed = await management.call(rt, run, 'skills_list', {'agent_id': worker.id})
        read = await management.call(rt, run, 'skills_list', {'agent_id': worker.id, 'name': 'file-brief'})
        with pytest.raises(service.TeamError) as exc:
            await management.call(rt, run, 'skill_manage', {**args, 'request_id': 'duplicate'})
        return {'saved': saved, 'replay': replay, 'listed': listed, 'read': read, 'code': exc.value.code}
    seen = await run_tools(harness, operation)
    assert seen['saved']['skill']['enabled'] is False
    assert seen['replay']['replayed'] is True and len(seen['listed']['skills']) == 1
    assert seen['code'] == 'name_taken' and 'cite each finding' in seen['read']['body']


async def test_profile_rejects_privileged_fields_without_writing(harness, sessions, owner):
    async def operation(rt, run, worker):
        async with sessions() as db:
            revision = setup.revision(await db.get(Agent, worker.id))
        for field in ('approval_mode', 'make_lead', 'memory_enabled', 'delegation_enabled'):
            with pytest.raises(service.TeamError) as exc:
                await management.call(rt, run, 'propose_profile', {'agent_id': worker.id, 'expected_revision': revision, 'fields': {field: True}})
            assert exc.value.code == 'invalid_input'
        return {'checked': True}
    assert (await run_tools(harness, operation))['checked']
    async with sessions() as db:
        assert not await db.scalar(select(TeamSetupRequest).where(TeamSetupRequest.owner_id == owner))


async def test_routine_stale_change_and_invalid_activation_fields_fail(harness, sessions, owner):
    async def operation(rt, run, worker):
        saved = await management.call(rt, run, 'create_routine', {'agent_id': worker.id, 'name': 'Paused work', 'prompt': 'Read local files', 'schedule_kind': 'manual', 'timezone': 'UTC'})
        row = saved['routine']
        with pytest.raises(service.TeamError) as exc:
            await management.call(rt, run, 'manage_routine', {'agent_id': worker.id, 'routine_id': row['id'], 'expected_revision': 'outdated', 'action': 'resume'})
        assert exc.value.code == 'changed_since'
        with pytest.raises(service.TeamError) as exc:
            await management.call(rt, run, 'create_routine', {'agent_id': worker.id, 'name': 'No automatic activation', 'prompt': 'x', 'paused': False})
        assert exc.value.code == 'invalid_input'
        return {'id': row['id']}
    seen = await run_tools(harness, operation)
    async with sessions() as db:
        assert (await db.get(Routine, seen['id'])).paused is True


def _profile_decision(team, args, mode='ask'):
    return policy.automatic('mcp__team__propose_profile', mode, team, args)


def test_profile_auto_allows_only_own_text_edits():
    member = policy.TurnTeam(1, 5, False, False, profile_auto=True)
    assert _profile_decision(member, {'fields': {'instructions': 'Be brief.'}, 'expected_revision': 'x'}) == ('allowed', 'team_profile_auto')
    assert _profile_decision(member, {'agent_id': 5, 'fields': {'title': 'Ops', 'name': 'Ops'}}) == ('allowed', 'team_profile_auto')
    # Folder, another agent, empty or unknown fields still need the person.
    assert _profile_decision(member, {'fields': {'workspace_path': '/tmp'}}) is None
    assert _profile_decision(member, {'fields': {'instructions': 'x', 'workspace_path': '/tmp'}}) is None
    assert _profile_decision(member, {'agent_id': 6, 'fields': {'instructions': 'x'}}) is None
    assert _profile_decision(member, {'fields': {}}) is None
    assert _profile_decision(member, {}) is None


def test_profile_auto_is_off_by_default_and_respects_restrictions():
    args = {'fields': {'instructions': 'Be brief.'}}
    assert _profile_decision(policy.TurnTeam(1, 5, False, False), args) is None
    on = policy.TurnTeam(1, 5, False, False, origin='webhook', profile_auto=True)
    assert _profile_decision(on, args) == ('denied', 'configuration_forbidden')
    assert _profile_decision(policy.TurnTeam(1, 5, False, False, profile_auto=True), args, mode='plan') == ('denied', 'configuration_forbidden')


async def test_skill_changes_have_receipts_and_revision_bound_undo(harness, sessions, owner, monkeypatch, tmp_path):
    from app.services import agent_memory, agent_skills
    monkeypatch.setattr(agent_memory, 'agent_home', lambda agent_id: tmp_path / str(agent_id))
    monkeypatch.setattr(agent_skills, 'agent_home', lambda agent_id: tmp_path / str(agent_id))
    async def operation(rt, run, worker):
        out = {}
        create = {'agent_id': worker.id, 'action': 'create', 'name': 'brief', 'description': 'First', 'body': 'Original steps.', 'request_id': 'sk-create'}
        out['created'] = await management.call(rt, run, 'skill_manage', create)
        async with sessions() as db:
            row = await setup.existing(db, rt.owner_id, 'sk-create')
            out['kind'] = row.kind
            out['status_created'] = await setup.undo_status(db, row)
        revision = setup.digest(['First', 'Original steps.', False])[:16]
        out['updated'] = await management.call(rt, run, 'skill_manage', {'agent_id': worker.id, 'action': 'update', 'name': 'brief', 'description': 'Second', 'body': 'Changed steps.', 'expected_revision': revision, 'request_id': 'sk-update'})
        async with sessions() as db:
            out['undo_update'] = await setup.undo(db, rt.owner_id, 'sk-update')
        out['restored'] = agent_skills.read_skill(worker.id, 'brief').body
        # The creation receipt is now stale: the skill's revision moved on with the restore.
        async with sessions() as db:
            out['stale_after_restore'] = await setup.undo_status(db, await setup.existing(db, rt.owner_id, 'sk-create'))
        # A person edit after a change makes Undo refuse and leaves the edit alone.
        out['again'] = await management.call(rt, run, 'skill_manage', {'agent_id': worker.id, 'action': 'update', 'name': 'brief', 'description': 'Third', 'body': 'Third steps.', 'expected_revision': setup.digest(['First', 'Original steps.', False])[:16], 'request_id': 'sk-third'})
        agent_skills.write_skill(worker.id, 'brief', 'Mine', 'Person edit.', enabled=True)
        async with sessions() as db:
            with pytest.raises(service.TeamError) as exc:
                await setup.undo(db, rt.owner_id, 'sk-third')
        out['refused'] = exc.value.code
        out['kept'] = agent_skills.read_skill(worker.id, 'brief').body
        # Removing a created skill works when untouched.
        await management.call(rt, run, 'skill_manage', {'agent_id': worker.id, 'action': 'create', 'name': 'fresh', 'description': 'New', 'body': 'Steps.', 'request_id': 'sk-fresh'})
        async with sessions() as db:
            out['undo_create'] = await setup.undo(db, rt.owner_id, 'sk-fresh')
        out['fresh_exists'] = any(item.name == 'fresh' for item in agent_skills.list_skills(worker.id))
        return out
    seen = await run_tools(harness, operation)
    assert seen['kind'] == 'skill' and seen['created']['action'] == 'create' and seen['created']['undo_note']
    assert seen['status_created']['available'] is True
    assert seen['updated']['skill']['before']['description'] == 'First'
    assert seen['undo_update']['state'] == 'undone' and seen['restored'] == 'Original steps.'
    assert seen['stale_after_restore']['available'] is False
    assert seen['refused'] == 'changed_since' and seen['kept'] == 'Person edit.'
    assert seen['undo_create']['removed'] is True and seen['fresh_exists'] is False


async def test_chat_changes_and_their_undo_are_recorded_in_agent_history(harness, sessions, owner):
    from app.services import agent_history
    async def operation(rt, run, worker):
        async with sessions() as db:
            revision = setup.revision(await db.get(Agent, worker.id))
        applied = await management.call(rt, run, 'propose_profile', {'agent_id': worker.id, 'expected_revision': revision, 'fields': {'instructions': 'Answer in one line.'}, 'request_id': 'hist-1'})
        async with sessions() as db:
            first = [(r.number, r.source, r.changed_fields) for r in await agent_history.list_revisions(db, worker.id)]
            await setup.undo(db, rt.owner_id, 'hist-1')
        async with sessions() as db:
            second = [(r.number, r.source, r.changed_fields) for r in await agent_history.list_revisions(db, worker.id)]
        return {'applied': applied['state'], 'first': first, 'second': second}
    seen = await run_tools(harness, operation)
    assert seen['applied'] == 'applied'
    # Baseline plus the chat change; Undo adds its own entry instead of rewriting history.
    assert [(n, s) for n, s, _ in seen['first']] == [(2, 'chat'), (1, 'baseline')]
    assert 'instructions' in seen['first'][0][2]
    assert [(n, s) for n, s, _ in seen['second']][:2] == [(3, 'chat_undo'), (2, 'chat')]
