"""Mnemos connectors: pull Confluence, Jira and web content into ``mnemos_knowledge``.

Each connector lists one scope (space, project, host) as
:class:`connectors.sync.SourceItem` objects; :func:`connectors.sync.sync_scope`
mirrors them into a Mnemos server over its HTTP API.
"""
