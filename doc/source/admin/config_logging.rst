Logging Configuration
========================================

Overview
----------------------------

There are two ways in which you can configure logging for Galaxy servers:

1. Basic/automatic configuration with control over log level and log destination (standard output or a named log file).
2. More complex configuration using the Python :mod:`logging` module's :func:`logging.config.dictConfig` or :func:`logging.config.fileConfig`.

Basic Configuration
----------------------------

Basic logging configuration can be used to modify the level of log messages and the file to which Galaxy logs.

The logging level is controlled by the ``log_level`` configuration option. By default, Galaxy logs all messages at the
``DEBUG`` level.

Galaxy logs all messages to standard output by default if running in the foreground. If running in the background (e.g.
by passing the ``--daemon`` argument to ``run.sh``), the log is written to a location configured in
`gravity <https://github.com/galaxyproject/gravity/>`_.

Gravity and related terminology are explained in detail in the :doc:`Scaling and Load Balancing <scaling>` documentation.

**Setting the log level:**

In ``galaxy.yml``, set ``log_level``:

.. code-block:: yaml

    galaxy:
        log_level: LEVEL

Where ``LEVEL`` is one of the `logging levels`_ documented in the :mod:`logging` module.

**Logging to a file:**

To change the log file name or location, use the ``$GALAXY_LOG`` environment variable like so:

.. code-block:: shell-session

    $ GALAXY_LOG=/path/to/galaxy/logfile sh run.sh --daemon

It is also possible to specify the path to the log file using the ``log_destination`` configuration option in
``galaxy.yml``. Additionally, it is possible to automatically rotate logs once the log file reaches a given size, using
the ``log_rotate_size`` and ``log_rotate_count`` options, which control the size at which the log is rotated, and the
number of rotated logs to keep, respectively:

.. code-block:: yaml

    galaxy:
        # Set log file path
        log_destination: /srv/galaxy/log/galaxy.log
        # Rotate once log reaches 100 MB
        log_rotate_size: 100 MB
        # Keep the 10 most recent log files
        log_rotate_count: 10

Advanced Configuration
----------------------------

For more useful and manageable logging when running Galaxy with forking application stacks where multiple
Galaxy server processes are forked after the Galaxy application is loaded, a custom ``filename_template`` config option
is available to :class:`logging.FileHandler` (or derivative class) log handler definitions so that multiple file logging is possible.
Without this custom option, all forked Galaxy server processes would only be able to log to a single combined log file,
which can be very difficult to work with.

YAML
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Advanced logging configuration is performed under the ``logging`` key in the ``galaxy`` section of ``galaxy.yml``. The
syntax is a YAML dictionary in the syntax of Python's :func:`logging.config.dictConfig`. This section covers a few of
the most common configurations as well as Galaxy's customizations. Consult the :func:`logging.config.dictConfig`
documentation for a complete explanation of the syntax and possibilities.

Default
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The default as of this Galaxy release can be found (in Python syntax) in the
:data:`galaxy.config.LOGGING_CONFIG_DEFAULT` constant and (in YAML) below:

.. include:: config_logging_default_yaml.rst

Audit events
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

With ``audit_log`` enabled, Galaxy writes one JSON object per line to the ``galaxy.audit`` logger at ``INFO`` for
each audited action: the ways data leaves Galaxy, and the ways it changes hands inside Galaxy. Actions are grouped
into families by the part of their name before the dot, which is what the ``actions`` setting under ``audit_log``
selects:

``dataset``
    Content served by the dataset API: ``dataset.display`` and ``dataset.download`` (``/api/datasets/{id}/display``,
    ``/download``, ``/extra_files/raw/{path}`` and their history contents equivalents), ``dataset.download_url`` when
    ``/download`` hands out an object store's presigned URL (the event records the URL's host and lifetime, never the
    URL), ``dataset.download_metadata_file``, ``dataset.list_extra_files``, ``dataset.read_text``
    (``get_content_as_text`` and the tool ``report``), and ``dataset.read_data`` (datatype reads through
    ``?data_type=`` and ``/content/{type}``). Links handed to external display sites -- ``display_at``, display
    applications and ``display_as`` -- are ``dataset.external_link`` when the link is issued and
    ``dataset.external_fetch`` when the external site fetches the content. Also in this family: ``dataset.export``
    (a dataset exported from a history), ``dataset.permissions`` (a permission change, with the access and manage
    roles before and after, and whether access may have widened) and ``dataset.copy`` (a copy of a dataset owned by
    someone else, including copies made while building or copying a collection; a collection that fails part way
    still records the copies already committed).
``library_dataset``
    Library downloads, one event per dataset in the request.
``drs``
    GA4GH DRS object lookups (``drs.object``) and ``/api/drs_download`` (``drs.download``).
``history``
    ``history.export`` (to a download or a remote file source), ``history.download`` (a contents archive),
    ``history.share`` (sharing with users, link access, publishing and slug changes, with the state before and
    after; the after state is what the request itself wrote and committed, so a concurrent change by another
    request is never attributed to it) and ``history.import`` (a copy of someone else's history).
``collection``
    ``collection.download`` (a collection zip) and ``collection.export``.
``invocation``
    ``invocation.export``, including exports a workflow queues when it finishes.
``archive``
    ``archive.download``: a prepared export archive fetched from short-term storage or from a legacy history export.
``workflow``, ``page``, ``visualization``
    ``workflow.share``, ``page.share`` and ``visualization.share``, as for histories.

An export is recorded when it is requested, since the work runs later in a task or job. Its event carries the
``task_id`` or ``job_id`` that will do it and, for a download, ``storage_request_digest``: the first 32 hex characters
of the SHA-256 of the short-term storage request id, which the later ``archive.download`` event carries too. Neither
event records the id itself, because anyone holding it can fetch the archive. Remote export targets are recorded
without credentials, query or fragment.

Not yet audited: the legacy ``/dataset/display`` controller, library and folder permission changes, publishing a
workflow through ``PUT /api/workflows/{id}``, role and group membership changes, and page or invocation PDFs. A
request refused before it reaches the route (a bad API key, a refused ``run_as``) records no event. Each event
names the authenticated actor and the effective user -- they differ under ``run_as`` and in a session created by
impersonation -- along with how the request authenticated, the request id (the same id as the access log line and the
``X-Request-ID`` response header), the client address, the object acted on, and an ``outcome``. Work Galaxy does in
the background for a user, such as an export a workflow queues when it finishes, is recorded with ``auth.method`` set
to ``task``: the effective user is the one it runs for, and there is no actor, credential or request.

``success``
    For content, the response started with a status below 400: it was handed to the application server (or, with
    ``nginx_x_accel_redirect_base`` or ``apache_xsendfile``, to the proxy, which then validates any ``Range`` header
    and reads the file itself). It does not prove every byte was delivered, or even that the client was still
    connected: the application server may accept a response after the client has gone. For routes that return a JSON
    body, and on legacy (non-FastAPI) routes, ``success`` is recorded when the route hands its result to the server;
    for a presigned redirect it means the redirect carrying the URL started; for an export, that the work was queued, not that it
    finished; and for a change such as a share or a permission update, that the change was committed.
``denied``
    Galaxy refused the request. The event names the requested object by id.
``error``
    The request was not refused but did not complete, with a short ``reason`` (``not_found``, ``invalid_range``,
    ``archive_failed``, ``response_not_started``, ...) and the ``stage`` it failed at (``authorize``, ``prepare`` or ``respond``).

By default events carry numeric and encoded ids only. Set ``include_names: true`` under ``audit_log`` to also record
usernames, email addresses, the names of datasets, histories, files and shared items (workflows, pages,
visualizations), and sharing slugs. Strings are JSON-escaped to ASCII, so user-supplied
values cannot break a line apart. An event over 4 KiB loses its user agent, names and details, and its ``truncated``
field says ``optional_fields``; identifiers are never dropped. If it is still over 4 KiB after that (only possible with
very long admin-configured values such as the instance URL), it is written anyway and marked ``over_budget``, rather
than cut into invalid JSON.

Under the default logging configuration these lines go to the console along with everything else. To keep them
separate, give ``galaxy.audit`` its own handler, a message-only formatter, and ``propagate: false``. This example
sends them to the local syslog daemon, which can forward them off the host:

.. code-block:: yaml

    galaxy:
        audit_log:
            enabled: true
        logging:
            version: 1
            disable_existing_loggers: false
            root:
                handlers: [console]
                level: INFO
            formatters:
                stack:
                    (): galaxy.web_stack.application_stack_log_formatter
                audit:
                    format: "%(message)s"
            handlers:
                console:
                    class: logging.StreamHandler
                    formatter: stack
                    level: INFO
                    stream: ext://sys.stderr
                audit:
                    class: logging.handlers.SysLogHandler
                    address: /dev/log
                    facility: auth
                    formatter: audit
                    level: INFO
            loggers:
                galaxy.audit:
                    handlers: [audit]
                    level: INFO
                    propagate: false

A failed audit write never fails the request. If building or writing an event fails inside Galaxy, or ``audit_log`` is
enabled but no handler accepts ``galaxy.audit`` at ``INFO``, Galaxy logs an error on its own log and counts it (as
``galaxy.audit.failures``, under ``statsd_prefix``, when ``statsd_host`` is set); the unrouted case is also a warning at
startup. Once a handler has accepted an event, failures in it (a full disk, a dead syslog socket) are reported the way
Python's ``logging`` reports any handler error, on stderr. Detect those as gaps at the log platform.

Audit events can come from any web worker, so route them to something that accepts concurrent writers (syslog,
journald, a log shipper) rather than a single shared rotating file. Behind a reverse proxy, the recorded client
address is the proxy's unless the application server trusts its forwarded headers. Identity is only recorded for
requests served through Galaxy's ASGI application; a deployment serving the legacy WSGI application on its own gets
events without identity or request fields.

.. _logging levels: https://docs.python.org/library/logging.html#logging-levels
.. _fileConfig file format: https://docs.python.org/library/logging.config.html#configuration-file-format
