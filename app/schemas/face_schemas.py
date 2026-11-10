"""Input shapes for the face registration endpoints.

Photos arrive as data URLs ("data:image/jpeg;base64,..."), which is what a
browser canvas produces. The number of photos and their content are checked by
face_service, so the rule lives in one place.
"""
from marshmallow import fields, validate

from .common import StrictSchema

# ~3 MB image as base64 is ~4 MB of text; a little headroom for the "data:" prefix.
MAX_DATA_URL_LENGTH = 4_300_000


def _data_url():
    return fields.String(required=True, validate=validate.Length(min=1, max=MAX_DATA_URL_LENGTH))


class FaceCheckSchema(StrictSchema):
    image = _data_url()


class FaceEnrolSchema(StrictSchema):
    images = fields.List(_data_url(), required=True, validate=validate.Length(min=1, max=10))


class FaceResetSchema(StrictSchema):
    reason = fields.String(required=True, validate=validate.Length(min=3, max=200))