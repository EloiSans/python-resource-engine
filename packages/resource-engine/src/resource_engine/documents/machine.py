from __future__ import annotations

from statemachine import State, StateMachine


class DocumentStateMachine(StateMachine):
    """Life of a document from upload to deletion.

    States:
        pending: Upload requested; the content is not confirmed yet.
        processing: Stored and waiting for the processing pipeline.
        available: Stored, verified and processed; it can be downloaded.
        rejected: The pipeline refused it (see ``error``).
        deleted: Detached; the content is purged from the store.

    Transitions:
        confirm: The content is stored and needs no processing.
        start_processing: The content is stored and goes to the pipeline.
        complete: The pipeline accepted the document.
        reject: The pipeline or the upload check refused it.
        discard: The document is detached or its upload expired.
    """

    pending = State("Pending", initial=True)
    processing = State("Processing")
    available = State("Available")
    rejected = State("Rejected")
    deleted = State("Deleted", final=True)

    confirm = pending.to(available)
    start_processing = pending.to(processing)
    complete = processing.to(available)
    reject = pending.to(rejected) | processing.to(rejected)
    discard = (
        pending.to(deleted) | processing.to(deleted) | available.to(deleted) | rejected.to(deleted)
    )
