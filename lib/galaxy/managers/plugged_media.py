"""
Manager and Serializer for plugged media.
"""

from galaxy import model
from galaxy import exceptions
from galaxy.managers import datasets
from galaxy.managers import deletable
from galaxy.managers import hdas
from galaxy.managers import sharable

import logging
log = logging.getLogger(__name__)


class PluggedMediaManager(sharable.SharableModelManager, deletable.PurgableManagerMixin):

    model_class = model.PluggedMedia
    foreign_key_name = 'plugged_media'

    def __init__(self, app, *args, **kwargs):
        super(PluggedMediaManager, self).__init__(app, *args, **kwargs)
        self.hda_manager = hdas.HDAManager(app)
        self.dataset_manager = datasets.DatasetManager(app)

    def purge(self, plugged_media):
        """
        Purges a plugged media by taking the following steps:
        (1) marks the plugged media `purged` in the database;
        (2) deletes all the datasets persisted on the plugged media;
        (3) marks all the HDAs associated with the deleted datasets as purged.
        This operation does NOT `delete` the plugged media physically
        (e.g., it does not delete a S3 bucket), because the plugged media
        (e.g., a S3 bucket) may contain data other than those loaded
        or mounted on Galaxy which deleting the media (e.g., deleting
        a S3 bucket) will result in unexpected file deletes.
        :param plugged_media: The media to be purged.
        :return: returns the purged plugged media.
        """
        if not plugged_media.purgeable:
            raise exceptions.ConfigDoesNotAllowException(
                "The plugged media (ID: `{}`; category: `{}`) is not purgeable."
                .format(plugged_media.id, plugged_media.category))
        for assoc in plugged_media.data_association:
            for hda in assoc.dataset.history_associations:
                self.hda_manager.purge(hda)
            self.dataset_manager.purge(assoc.dataset, plugged_media=plugged_media)
        plugged_media.purged = True
        self.session().flush()
        return plugged_media


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
            'create_time',
            'update_time',
            'usage',
            'hierarchy',
            'quota',
            'percentile',
            'category',
            'path',
            'deleted',
            'purged',
            'purgeable'
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
            'id'         : lambda i, k, **c: self.app.security.encode_id(i.id),
            'model_class': lambda *a, **c: 'PluggedMedia',
            'user_id'    : lambda i, k, **c: self.app.security.encode_id(i.user_id),
            'usage'      : lambda i, k, **c: i.usage,
            'hierarchy'  : lambda i, k, **c: i.hierarchy,
            'quota'      : lambda i, k, **c: i.quota,
            'percentile' : lambda i, k, **c: i.percentile,
            'category'   : lambda i, k, **c: i.category,
            'path'       : lambda i, k, **c: i.path,
            'deleted'    : lambda i, k, **c: i.deleted,
            'purged'     : lambda i, k, **c: i.purged,
            'purgeable'  : lambda i, k, **c: i.purgeable
        })
