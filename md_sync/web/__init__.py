"""Web dashboard for md-sync — reproduce ``index.html`` as the live UI.

The browser is the user's "output config" surface: it drives the same
:class:`~md_sync.core.pipeline.SyncPipeline` the CLI and Qt GUI use, but with
no on-disk ``md-sync.yaml`` required. State is held in-memory in a
:class:`WebSession`; realtime sync logs stream over Server-Sent Events.
"""
