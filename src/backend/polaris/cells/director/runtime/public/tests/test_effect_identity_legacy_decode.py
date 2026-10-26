"""Compatibility probe for old Python DTO pickles, separate from persisted JSON."""

from __future__ import annotations

import base64
import pickle

from polaris.cells.director.runtime.public import (
    directed_effect_contracts as contracts,
    directed_effect_policy_contracts as policy,
)


def test_old_director_immutable_python_pickles_decode_through_public_aliases() -> None:
    old_map = (
        "gASVdQAAAAAAAACMP3BvbGFyaXMuY2VsbHMuZGlyZWN0b3IucnVudGltZS5wdWJsaWMuZGlyZWN0ZWRf"
        "ZWZmZWN0X2NvbnRyYWN0c5SMHERpcmVjdGVkRWZmZWN0SW1tdXRhYmxlTWFwVjGUk5QpgZRdlIwBeJRLAYaUhZRhYi4="
    )
    old_sequence = (
        "gASVfgAAAAAAAACMP3BvbGFyaXMuY2VsbHMuZGlyZWN0b3IucnVudGltZS5wdWJsaWMuZGlyZWN0ZWRf"
        "ZWZmZWN0X2NvbnRyYWN0c5SMIURpcmVjdGVkRWZmZWN0SW1tdXRhYmxlU2VxdWVuY2VWMZSTlCmBlF2UiEsBRz/wAAAAAAAAh5RhYi4="
    )
    decoded_map = pickle.loads(base64.b64decode(old_map))
    decoded_sequence = pickle.loads(base64.b64decode(old_sequence))
    assert type(decoded_map) is contracts.DirectedEffectImmutableMapV1
    assert type(decoded_sequence) is contracts.DirectedEffectImmutableSequenceV1
    assert decoded_map.items == (("x", 1),)
    assert decoded_sequence.items == (True, 1, 1.0)


def test_old_director_subject_python_pickle_preserves_operation_digest() -> None:
    old_subject = (
        "gASVOgEAAAAAAACMRnBvbGFyaXMuY2VsbHMuZGlyZWN0b3IucnVudGltZS5wdWJsaWMuZGlyZWN0ZWRfZWZmZWN0X3BvbGljeV9j"
        "b250cmFjdHOUjCZEaXJlY3RvckVmZmVjdFBvbGljeU9wZXJhdGlvblN1YmplY3RWMZSTlCmBlF2UKIwOL3dvcmtzcGFjZS/ml6eU"
        "jAR0dXJulIwFYmF0Y2iUjARjYWxslEsAjAp3cml0ZV9maWxllIwHY29udGVudJSMB+axifCfmYKUhpSMBGZpbGWUjAlzcmMvYS50"
        "eHSUhpSGlIwFd3JpdGWUjAx3cml0ZV9zZXJpYWyUjEBhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFh"
        "YWFhYWFhYWFhYWFhYWFhYWFhYWFhlGViLg=="
    )
    subject = pickle.loads(base64.b64decode(old_subject))
    assert type(subject) is policy.DirectorEffectPolicyOperationSubjectV1
    assert policy.hash_director_effect_policy_operation_subject(subject) == (
        "1f1af97c73e0b3894e0979a08cf2172b79be7fabcaabffef619c7e6e50f8d6fc"
    )
