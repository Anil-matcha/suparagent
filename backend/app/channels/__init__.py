"""Chat channels: the owner talks to an agent from a chat app (Slack first).

`bridge.py` holds everything that does not depend on the chat app (which
conversation a message goes to, relaying a turn's events back, chunking,
approval cards); an adapter in `base.py`'s shape holds the chat app itself.
Design notes: .internal/design/slack-channel.md (private).
"""
