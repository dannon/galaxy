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


class PluggedMediaSerializer(sharable.SharableModelSerializer, deletable.PurgableSerializerMixin):
    """
    Interface/service object for serializing plugged media into dictionaries.
    """
    model_manager_class = PluggedMediaManager

    def __init__(self, app, **kwargs):
        super(PluggedMediaSerializer, self).__init__(app, **kwargs)
        self.plugged_media_manager = self.manager

        self.default_view = 'summary'
        self.add_view('summary', [
            'id',
            'model_class',
            'user_id',
            'hierarchy',
            'category',
            'path',
            'deleted',
            'purged',
            'purgable'
        ])

    def add_serializers(self):
        super(PluggedMediaSerializer, self).add_serializers()
        deletable.PurgableSerializerMixin.add_serializers(self)

        # Each key (e.g., 'hierarchy') receives a set of arguments, and the value of the key
        # can be dependent (e.g., see 'hierarchy') or independent (e.g., see 'model_class') from the input arguments.
        # see: serialize function in lib/galaxy/managers/base.py
        #
        # Arguments of the following lambda functions:
        # i  : an instance of galaxy.model.PluggedMedia.
        # k  : serialized dictionary key (e.g., 'model_class', 'hierarchy', 'category', and 'path').
        # **c: a dictionary containing 'trans' and 'user' objects.
        self.serializers.update({
            'id'         : lambda i, k, **c: i.id,
            'model_class': lambda *a, **c: 'PluggedMedia',
            'user_id'    : lambda i, k, **c: i.user_id,
            'hierarchy'  : lambda i, k, **c: i.hierarchy,
            'category'   : lambda i, k, **c: i.category,
            'path'       : lambda i, k, **c: i.path,
            'deleted'    : lambda i, k, **c: i.deleted,
            'purged'     : lambda i, k, **c: i.purged,
            'purgable'   : lambda i, k, **c: i.purgable
        })
