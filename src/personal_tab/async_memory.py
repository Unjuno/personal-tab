"""Bounded CPU learning and versioned snapshot adoption for a local editor.

Runtime is stdlib only. Canonical edits are never coalesced: only superseded
*training requests* are. A request contains the complete current text/origin
snapshot, so skipping a job cannot skip typed characters. Only the main process
answers predictions. It never waits for training or IPC serialization.

This is experimental statistical/term learning, not asynchronous neural training.
All text is in RAM; no external pickle or model file is accepted by this module.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import threading
import time
from typing import Any

from personal_tab.core import StreamModel, TraceStore
from personal_tab.term_model import TermMemory, script


class MemoryBuilder:
    """Worker-owned models, updated incrementally when a snapshot only appends."""
    def __init__(self) -> None:
        self.epoch = -1
        self.previous: dict[str, tuple[str, tuple[str, ...], int]] = {}
        self.model = StreamModel(mode='pooled', order=4, alpha=1.)
        self.terms = TermMemory(minimum_prefix=3, minimum_count=2, max_length=4)
        self.active = ''

    def _observe(self, char: str, origin: str) -> None:
        if origin == 'typed':
            self.model.observe(char)
            self.terms.observe(char)
        else:
            # Context may include pasted/model content; it is never a target.
            self.model.context = (self.model.context + char)[-self.model.order:]
            self.terms.pending = ''
            self.terms.kind = None

    def _activate(self, doc: str, text: str, origins: tuple[str, ...]) -> None:
        if self.active == doc:
            return
        self.model.begin_doc(doc)
        self.terms.begin_doc(doc)
        self.model.context = text[-self.model.order:]
        # Restore an unfinished typed term without counting it a second time.
        if text and origins[-1] == 'typed' and (kind := script(text[-1])):
            suffix = []
            for c, origin in zip(reversed(text), reversed(origins)):
                if origin != 'typed' or script(c) != kind:
                    break
                suffix.append(c)
            self.terms.pending = ''.join(reversed(suffix))
            self.terms.kind = kind
        self.active = doc

    def learn(self, job: dict[str, Any]) -> dict[str, Any]:
        start = time.perf_counter_ns()
        docs = {d: (text, origins, version) for d, text, origins, version in job['documents']}
        can_append = job['epoch'] == self.epoch and set(self.previous) <= set(docs)
        if can_append:
            for doc, (old, old_origins, _) in self.previous.items():
                text, origins, _ = docs[doc]
                if not text.startswith(old) or origins[:len(old)] != old_origins:
                    can_append = False
                    break
        if not can_append:
            self.model = StreamModel(mode='pooled', order=4, alpha=1.)
            self.terms = TermMemory(minimum_prefix=3, minimum_count=2, max_length=4)
            self.previous = {}
            self.active = ''
        learned = 0
        for doc, (text, origins, _) in docs.items():
            old, old_origins, _ = self.previous.get(doc, ('', (), 0))
            if len(text) > len(old):
                self._activate(doc, old, old_origins)
                for char, origin in zip(text[len(old):], origins[len(old):]):
                    self._observe(char, origin)
                    learned += origin == 'typed'
        self.previous = docs
        self.epoch = job['epoch']
        return {
            'request_id': job['request_id'], 'epoch': self.epoch,
            'model': self.model, 'terms': self.terms,
            'versions': {d: v for d, (_, _, v) in docs.items()},
            'update_kind': 'append' if can_append else 'rebuild',
            'learned_this_job': learned, 'pid': os.getpid(),
            'learning_ms': (time.perf_counter_ns() - start) / 1e6,
        }


def _worker_entry(connection, delay: float) -> None:
    """Only trusted parent snapshots cross this private pipe."""
    builder = MemoryBuilder()
    try:
        while True:
            job = connection.recv()
            if job is None:
                return
            if delay:
                time.sleep(delay)  # injected latency for tests, not a product setting
            try:
                result = builder.learn(job)
            except Exception as exc:
                # No traceback or payload text in logs/errors.
                result = {'request_id': job['request_id'], 'epoch': job['epoch'],
                          'error': type(exc).__name__}
            connection.send(result)
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()


class SnapshotWorker:
    """Bounded CPU-worker supervision with latest-full-snapshot replay.

    All canonical edits live in AsyncSession; only complete model-build jobs
    are coalesced. Unexpected process death is recovered on the IPC thread,
    never on the foreground request thread.
    """
    MAX_RESTARTS = 3

    def __init__(self, *, delay: float = 0.) -> None:
        self._cv = threading.Condition()
        self._pending = None
        self._latest = None
        self._result = None
        self._closed = False
        self._error = None
        self._delay = delay
        self._restarts = 0
        self._recovering = False
        self._submitted = self._coalesced = self._completed = 0
        self._completed_id = self._in_flight = 0
        self._context = mp.get_context('spawn')
        self._start_process()
        self._thread = threading.Thread(target=self._dispatch, name='tab-snapshot-ipc', daemon=True)
        self._thread.start()

    def _start_process(self) -> None:
        self._pipe, child = self._context.Pipe(duplex=True)
        self._process = self._context.Process(
            target=_worker_entry, args=(child, self._delay),
            name='personal-tab-learner', daemon=True)
        try:
            self._process.start()
        except BaseException:
            self._pipe.close()
            child.close()
            raise
        child.close()

    @property
    def alive(self) -> bool:
        return not self._closed and self._process.is_alive()

    def submit(self, job: dict[str, Any]) -> None:
        with self._cv:
            if self._closed or self._error:
                raise RuntimeError('learning worker unavailable; reset the session')
            if self._pending is not None:
                self._coalesced += 1
            self._pending = self._latest = job
            self._submitted += 1
            self._cv.notify_all()

    def _retire_process(self) -> None:
        try:
            self._pipe.close()
        except OSError:
            pass
        self._process.join(timeout=.1)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=.5)
        if self._process.is_alive():
            self._process.kill()
            self._process.join(timeout=.5)

    def _recover(self) -> bool:
        with self._cv:
            if self._closed:
                return False
            if self._restarts >= self.MAX_RESTARTS:
                self._error = 'learning worker repeatedly disconnected; reset the session'
                self._in_flight = 0
                return False
            self._restarts += 1
            self._recovering = True
            self._result = None
            self._in_flight = 0
            if self._latest is not None and (self._pending is None or
                                             self._pending['request_id'] < self._latest['request_id']):
                self._pending = self._latest
        try:
            self._retire_process()
            self._start_process()
        except (OSError, EOFError, RuntimeError):
            with self._cv:
                self._error = 'learning worker restart failed; reset the session'
                self._recovering = False
            return False
        with self._cv:
            if self._closed:
                return False
            self._recovering = False
            self._cv.notify_all()
        return True

    def _dispatch(self) -> None:
        try:
            while True:
                with self._cv:
                    self._cv.wait(timeout=.05)
                    if self._closed or self._error:
                        break
                    job, self._pending = self._pending, None
                    self._in_flight = job['request_id'] if job else 0
                if not self._process.is_alive():
                    if not self._recover():
                        break
                    continue
                if job is None:
                    continue
                try:
                    self._pipe.send(job)
                    while not self._pipe.poll(.05):
                        with self._cv:
                            if self._closed:
                                return
                        if not self._process.is_alive():
                            raise EOFError('worker exited')
                    result = self._pipe.recv()
                except (EOFError, BrokenPipeError, OSError, ValueError):
                    if not self._recover():
                        break
                    continue
                with self._cv:
                    self._result = result
                    self._in_flight = 0
                    self._completed += 1
                    self._completed_id = result['request_id']
                    if result.get('error'):
                        self._error = 'learning failed: ' + result['error']
                        break
        finally:
            try:
                self._pipe.send(None)
            except (OSError, ValueError, BrokenPipeError):
                pass

    def poll(self) -> dict[str, Any] | None:
        with self._cv:
            result, self._result = self._result, None
            return result

    def stats(self) -> dict[str, Any]:
        with self._cv:
            return {'pid': self._process.pid, 'alive': self.alive,
                    'recovering': self._recovering or not self._process.is_alive(),
                    'restarts': self._restarts,
                    'pending_count': int(self._pending is not None),
                    'in_flight': self._in_flight, 'submitted': self._submitted,
                    'coalesced': self._coalesced, 'completed': self._completed,
                    'completed_id': self._completed_id,
                    'error': self._error}

    def close(self) -> None:
        with self._cv:
            if self._closed:
                return
            self._closed = True
            self._pending = self._result = self._latest = None
            self._cv.notify_all()
        self._thread.join(timeout=.5)
        self._retire_process()
        self._thread.join(timeout=1)


class AsyncSession:
    """Serialized by the HTTP server lock. No training under that lock."""
    MAX_CHARS = 100_000
    MAX_DOCS = 32
    MAX_EVENTS = 20_000
    MAX_TRACE_CHARS = 1_000_000

    def __init__(self, *, worker_delay: float = 0.) -> None:
        self.trace = TraceStore()
        self.sequences: dict[str, int] = {}
        self.worker = SnapshotWorker(delay=worker_delay)
        self.snapshot = None
        self.epoch = 0
        self.request_id = 0
        self.rejected_snapshots = 0
        self._error = None
        self._last_generated = None
        self._trace_chars = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self) -> None:
        self.worker.close()
        self.snapshot = None

    def _schedule(self) -> None:
        self.request_id += 1
        job = {'request_id': self.request_id, 'epoch': self.epoch,
               'documents': tuple((d, text, tuple(self.trace.origins[d]), self.trace.versions[d])
                                  for d, text in self.trace.texts.items())}
        try:
            self.worker.submit(job)
        except RuntimeError as exc:
            self._error = str(exc)

    def _adopt(self) -> None:
        result = self.worker.poll()
        if result is None:
            return
        if result.get('error'):
            self._error = 'learning failed; reset the session'
        elif result['epoch'] == self.epoch and result['request_id'] <= self.request_id:
            if self.snapshot is None or result['request_id'] > self.snapshot['request_id']:
                self.snapshot = result
        else:
            self.rejected_snapshots += 1

    @staticmethod
    def _integer(value: Any, name: str) -> int:
        if type(value) is not int:
            raise ValueError(name + ' must be an integer')
        return value

    def apply(self, event: dict[str, Any]) -> dict[str, Any]:
        start_time = time.perf_counter_ns()
        if not isinstance(event, dict):
            raise ValueError('event must be an object')
        if event.get('allowed') is not True:
            return {'suggestion': '', 'recorded': False}
        doc = event.get('doc_id')
        if not isinstance(doc, str) or not doc or len(doc) > 128:
            raise ValueError('doc_id must be 1..128 characters')
        seq = self._integer(event.get('sequence'), 'sequence')
        if seq < 1:
            raise ValueError('sequence must be positive')
        mode = event.get('mode', 'terms')
        if mode not in {'terms', 'statistical'}:
            raise ValueError('invalid mode')
        kind = event.get('kind')
        if kind not in {'insert', 'replace', 'delete', 'delete_document', 'composition'}:
            raise ValueError('invalid kind')
        text = event.get('text', '')
        if not isinstance(text, str):
            raise ValueError('text must be a string')
        text.encode('utf-8', errors='strict')
        origin = event.get('origin', 'typed')
        if origin not in {'typed', 'paste', 'model'}:
            raise ValueError('invalid origin')
        start = self._integer(event.get('start', 0), 'start')
        end = self._integer(event.get('end', 0), 'end')
        last = self.sequences.get(doc, 0)
        if seq <= last:
            return {'duplicate': True, 'version': self.trace.versions.get(doc, 0), 'suggestion': ''}
        if seq != last + 1:
            raise ValueError('out-of-order event; resend missing event')
        old = self.trace.texts.get(doc, '')
        if kind not in {'composition', 'delete_document'} and not 0 <= start <= end <= len(old):
            raise ValueError('invalid character offsets')
        if len(self.trace.events) >= self.MAX_EVENTS:
            raise ValueError('event limit reached; export and reset the session')
        if doc not in self.sequences and len(self.sequences) >= self.MAX_DOCS:
            raise ValueError('document limit reached')
        inserted = '' if kind in {'delete', 'delete_document'} else text
        new_length = 0 if kind == 'delete_document' else len(old) - (end - start) + len(inserted)
        if kind != 'composition' and sum(map(len, self.trace.texts.values())) - len(old) + new_length > self.MAX_CHARS:
            raise ValueError('demo limit: 100000 total code points')
        if kind == 'composition' and len(text) > self.MAX_CHARS:
            raise ValueError('composition too large')
        if kind != 'composition' and self._trace_chars + len(inserted) > self.MAX_TRACE_CHARS:
            raise ValueError('trace size limit reached; export and reset the session')
        # All validation occurs before the canonical state is mutated.
        self.trace.apply(doc, kind, start=start, end=end, text=text, origin=origin)
        self.sequences[doc] = seq
        if kind != 'composition':
            self._trace_chars += len(inserted)
        self.trace.events[-1].update(sequence=seq, received_monotonic_ns=time.monotonic_ns())
        if kind == 'composition':
            return {'suggestion': '', 'version': self.trace.versions.get(doc, 0), 'recorded': True}
        if kind != 'insert' or start != end or end != len(old):
            # Old transitions may now be invalid. Fail closed until rebuilt.
            self.epoch += 1
            self.snapshot = None
            self._last_generated = None
        self._schedule()
        cursor = min(len(self.trace.texts.get(doc, '')), start + len(inserted))
        result = self.suggest(doc_id=doc, cursor=cursor, mode=mode)
        result.update(recorded=True, sequence=seq,
                      processing_ms=(time.perf_counter_ns() - start_time) / 1e6)
        return result

    def _learning_phase(self, worker_state: dict[str, Any]) -> str:
        """Single observable FSM for the training snapshot lifecycle.

        This is a derived state, not a second source of truth. Canonical
        versions/epochs and the worker's queue remain authoritative.
        """
        if self._error or worker_state['error']:
            return 'failed'
        if worker_state.get('recovering'):
            return 'recovering'
        if self.request_id == 0:
            return 'empty'
        if self.snapshot is None:
            if self.epoch > 0 and self.trace.texts:
                return 'rebuilding'
            if worker_state['in_flight']:
                return 'training'
            return 'queued'
        if self.snapshot['request_id'] < self.request_id:
            return 'updating'
        return 'ready'

    def suggest(self, *, doc_id: str, version: int | None = None,
                cursor: int | None = None, mode: str = 'terms') -> dict[str, Any]:
        start_time = time.perf_counter_ns()
        if mode not in {'terms', 'statistical'}:
            raise ValueError('invalid mode')
        self._adopt()
        current = self.trace.texts.get(doc_id, '')
        v = self.trace.versions.get(doc_id, 0)
        state = self.worker.stats()
        phase = self._learning_phase(state)
        result = {'suggestion': '', 'version': v, 'sequence': self.sequences.get(doc_id, 0),
                  'learning_phase': phase,
                  'epoch': self.epoch, 'trained_request': self.snapshot['request_id'] if self.snapshot else 0,
                  'learning_pending': (self.snapshot is None or self.snapshot['request_id'] < self.request_id or state['recovering']),
                  'learning_error': self._error or state['error'],
                  'typed_training_chars': self.snapshot['model'].long.seen if self.snapshot else 0,
                  'unique_words': len(self.snapshot['terms'].words) if self.snapshot else 0}
        if version is not None and version != v:
            result['stale'] = True
            return result
        if cursor is None:
            cursor = len(current)
        if type(cursor) is not int or not 0 <= cursor <= len(current):
            raise ValueError('invalid cursor')
        result['cursor'] = cursor
        if self.snapshot is not None and not state['recovering'] and not result['learning_error'] and doc_id in self.trace.texts:
            context = current[:cursor]
            if mode == 'terms':
                self.snapshot['terms'].doc = doc_id
                prediction = self.snapshot['terms'].suggest(context)
            else:
                prediction, scores = self.snapshot['model'].generate(context, max_chars=4)
                if len(prediction) < 4 or scores[-1] < .6:
                    prediction = ''
            result['suggestion'] = prediction
            key = (self.epoch, self.snapshot['request_id'], doc_id, v, cursor, mode, prediction)
            if prediction and key != self._last_generated and len(self.trace.events) < self.MAX_EVENTS:
                self.trace.events.append({'kind': 'suggestion_generated', 'doc_id': doc_id,
                                          'version': v, 'cursor': cursor, 'mode': mode,
                                          'text': prediction, 'sequence': self.sequences.get(doc_id, 0),
                                          'received_monotonic_ns': time.monotonic_ns()})
                self._last_generated = key
        result['processing_ms'] = (time.perf_counter_ns() - start_time) / 1e6
        return result

    def reset(self) -> None:
        self.trace = TraceStore()
        self.sequences = {}
        self.snapshot = None
        self.epoch += 1
        self._last_generated = None
        self._error = None
        self._trace_chars = 0
        if not self.worker.alive or self.worker.stats()['error']:
            self.worker.close()
            self.worker = SnapshotWorker()
        self._schedule()  # also clears worker-side text; late old epochs are rejected


    def export_memory(self) -> dict[str, Any]:
        """Explicit JSON snapshot of active text/provenance, never pickled weights."""
        documents = []
        for doc, text in self.trace.texts.items():
            spans = []
            for i, origin in enumerate(self.trace.origins[doc]):
                if spans and spans[-1]['origin'] == origin:
                    spans[-1]['end'] = i + 1
                else:
                    spans.append({'start': i, 'end': i + 1, 'origin': origin})
            documents.append({'id': doc, 'text': text, 'spans': spans})
        return {'schema': 'personal-tab-memory/v1', 'documents': documents}

    def restore_memory(self, payload: dict[str, Any]) -> None:
        """Validate completely before replacing state; malformed imports are atomic.

        Only explicitly selected user JSON enters here. Caller obtains consent.
        Schema preserves model/paste origin and does not load executable objects.
        """
        if not isinstance(payload, dict) or payload.get('schema') != 'personal-tab-memory/v1':
            raise ValueError('unsupported memory schema')
        documents = payload.get('documents')
        if not isinstance(documents, list) or len(documents) > self.MAX_DOCS:
            raise ValueError('invalid document list')
        fresh = TraceStore()
        total = 0
        for row in documents:
            if not isinstance(row, dict):
                raise ValueError('invalid document')
            doc, text, spans = row.get('id'), row.get('text'), row.get('spans')
            if not isinstance(doc, str) or not 1 <= len(doc) <= 128 or doc in fresh.texts:
                raise ValueError('invalid or duplicate document id')
            if not isinstance(text, str) or not isinstance(spans, list):
                raise ValueError('invalid text or provenance')
            text.encode('utf-8', errors='strict')
            total += len(text)
            if total > self.MAX_CHARS or len(spans) > len(text):
                raise ValueError('memory size limit reached')
            origins = []
            cursor = 0
            for span in spans:
                if not isinstance(span, dict):
                    raise ValueError('invalid provenance span')
                start = self._integer(span.get('start'), 'span start')
                end = self._integer(span.get('end'), 'span end')
                origin = span.get('origin')
                if start != cursor or not start < end <= len(text) or origin not in {'typed', 'paste', 'model'}:
                    raise ValueError('invalid provenance coverage')
                origins.extend([origin] * (end - start))
                cursor = end
            if cursor != len(text):
                raise ValueError('missing provenance')
            fresh.texts[doc] = text
            fresh.origins[doc] = origins
            fresh.versions[doc] = 1
            fresh.events.append({'kind': 'memory_imported', 'doc_id': doc, 'version': 1,
                                 'characters': len(text), 'received_monotonic_ns': time.monotonic_ns()})
        self.reset()
        self.trace = fresh
        self._schedule()
