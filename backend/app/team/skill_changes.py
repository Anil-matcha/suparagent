"""Skill receipt Undo: restores the previous saved text; never grants or enables anything new."""
from app.db.models import Agent
from app.services import agent_skills
from app.team import service

UNDO_NOTE = 'Undo restores the previous saved skill text, or removes a skill this created, if nobody has edited it since.'


def capture(found):
    """What Undo needs from the skill as it was; None when it did not exist or holds redacted text."""
    if found is None:
        return {'before': None, 'restorable': True}
    if found.redacted:
        return {'before': None, 'restorable': False}
    return {'before': {'description': found.description, 'body': found.body, 'enabled': found.enabled,
                       'source': found.source, 'triggers': list(found.triggers)}, 'restorable': True}


def entry(agent_id, name, action, found, saved):
    return {'agent_id': agent_id, 'name': name, 'action': action, 'revision': saved.revision, **capture(found)}


async def status(db, receipt, *, lock=False):
    if receipt.state != 'applied':
        return {'available': False, 'reason': 'This change is no longer undoable.'}
    item = receipt.undo or {}
    if not item:
        return {'available': False, 'reason': 'This change is no longer undoable.'}
    if not item.get('restorable'):
        return {'available': False, 'reason': 'Undo is unavailable because the earlier text contained secret-looking content.'}
    team = await service.team_for(db, receipt.owner_id)
    agent = await db.get(Agent, item['agent_id'], with_for_update=lock, populate_existing=True)
    if (team is None or team.id != receipt.team_id or agent is None or
            agent.owner_id != receipt.owner_id or agent.team_id != team.id or agent.archived_at is not None):
        return {'available': False, 'reason': 'The agent or team changed.'}
    try:
        current = agent_skills.read_skill(agent.id, item['name'])
    except agent_skills.SkillError:
        return {'available': False, 'reason': 'The skill no longer exists.'}
    if current.revision != item['revision']:
        return {'available': False, 'reason': 'The skill was edited after this change.'}
    return {'available': True, 'reason': None}


async def undo(db, owner, receipt, team):
    check = await status(db, receipt, lock=True)
    if not check['available']:
        await db.rollback()
        raise service.TeamError('changed_since', check['reason'])
    item = receipt.undo
    try:
        agent_skills.restore_skill(item['agent_id'], item['name'], item['before'], item['revision'])
    except agent_skills.SkillError as exc:
        await db.rollback()
        raise service.TeamError('changed_since', exc.message) from None
    removed = item['before'] is None
    receipt.state, receipt.decided_at = 'undone', service.now()
    receipt.result = {**receipt.result, 'undo_result': {'name': item['name'], 'removed': removed}}
    service.audit(db, team, 'skill_change_undone', agent_id=item['agent_id'],
                  data={'request_id': receipt.request_id, 'skill': item['name']})
    await db.commit()
    return {'request_id': receipt.request_id, 'state': 'undone', 'name': item['name'], 'removed': removed, 'note': UNDO_NOTE}
