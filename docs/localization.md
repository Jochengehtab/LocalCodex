# Localization keys

Python UI errors use `local_codex.i18n.tr()`. New request keys:
`request.queue_full`, `request.cancelled`, `request.timeout`, `request.continuation`,
`request.model_invalid`, `request.model_failed`. Model setup adds `setup.capability`
and `setup.cloud_model`. English and German are provided; English is the default.

The native monitor adds `license.notice` in its i18n table. Protocol identifiers,
configuration keys and stored route reasons remain stable and language independent.
