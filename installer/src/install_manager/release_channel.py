"""Static, embedded release selection; metadata cannot authorize executable hooks."""
import hashlib
import json
from pathlib import Path

V1_FIELDS = {'schema_version', 'product', 'release_id', 'version', 'channel', 'classification',
          'compatibility', 'profile', 'provenance', 'application_sha256', 'runtime_sha256',
          'model_digest', 'dependency_digest', 'activation_allowed'}
V2_FIELDS = {'schema_version', 'product', 'release_id', 'version', 'channel', 'classification',
             'compatibility', 'profile', 'provenance', 'application_sha256', 'runtime_sha256',
             'dependency_digest', 'component_catalog_digest', 'readiness_contract',
             'activation_allowed'}


def validate_channel(data: dict, *, qualification: bool, activation_capable: bool = False) -> dict:
    schema = data.get('schema_version')
    fields = V1_FIELDS if schema == 1 else V2_FIELDS if schema == 2 else set()
    if set(data) != fields or data.get('product') != 'LIC Lite':
        raise ValueError('unsupported channel metadata')
    if data['channel'] not in {'stable', 'candidate', 'pinned'}:
        raise ValueError('unknown release channel')
    if data['classification'] not in {'development-fixture', 'qualification-candidate', 'production-approved'}:
        raise ValueError('unknown artifact classification')
    if data['compatibility'] != 'known-compatible':
        raise ValueError('revoked or incompatible release refused')
    if data['activation_allowed'] is not activation_capable:
        raise ValueError('channel activation policy does not match this operation')
    if data['classification'] != 'production-approved' and not qualification:
        raise ValueError('this artifact is approved only for isolated qualification')
    digests = ['application_sha256', 'runtime_sha256', 'dependency_digest']
    digests += ['model_digest'] if schema == 1 else ['component_catalog_digest']
    for key in digests:
        value = data[key]
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('missing exact channel identity: ' + key)
    text_fields = ['release_id', 'version', 'profile', 'provenance']
    if schema == 2:
        text_fields.append('readiness_contract')
    if not all(isinstance(data[k], str) and data[k] for k in text_fields):
        raise ValueError('incomplete channel provenance')
    return data


def verify_delivery(root: Path, index_digest: str) -> dict:
    from .acquisition import sha256_file
    from .model_resources import safe_relative
    index = root / 'payload-index.json'
    if sha256_file(index) != index_digest:
        raise ValueError('delivered metadata index changed')
    record = json.loads(index.read_text(encoding='utf-8'))
    for name, expected in record['files'].items():
        safe_relative(name)
        path = root / name
        if path.resolve() != path.absolute() or not path.is_file() or sha256_file(path) != expected:
            raise ValueError('delivered payload changed: ' + name)
    return record
