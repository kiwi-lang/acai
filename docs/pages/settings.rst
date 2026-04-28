Settings
========

.. rubric:: Route: ``/settings``

Global system configuration, version management, and git backup.

Configuration
-------------

Editable settings loaded via ``getConfig`` / ``updateConfig``, organised
into sections:

* **Sandbox** — sandbox backend type (Docker, Podman, Bubblewrap, Nsjail).
* **Worker** — worker-related tunables.
* **Git** — repository and branch settings for backup/sync.

Version management
------------------

* Displays the current installed version.
* Checks PyPI for the latest release and shows an upgrade prompt when a
  newer version is available.
* **Trigger Update** — starts an SSE-streamed update process in-place.

Git backup
----------

* **Status** — shows whether a remote is configured and the sync state.
* **Generate SSH Key** — creates a deploy key for push access.
* **Setup Remote** — configure the remote repository URL.
* **Sync** — push the current workspace state to the remote.
* **Test Connection** — verify SSH connectivity.

Source
------

``acai/ui/src/components/SettingsPage.tsx``
