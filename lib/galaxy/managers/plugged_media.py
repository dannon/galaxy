"""
Manager and Serializer for plugged media.
"""

from galaxy import model
from galaxy.managers import sharable
from galaxy.managers import deletable

import logging
log = logging.getLogger(__name__)


class PluggedMediaManager(sharable.SharableModelManager, deletable.PurgableManagerMixin):

    model_class = model.PluggedMedia
    foreign_key_name = 'plugged_media'

    def __init__(self, app, *args, **kwargs):
        super(PluggedMediaManager, self).__init__(app, *args, **kwargs)
