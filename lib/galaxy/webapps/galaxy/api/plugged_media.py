"""
API operations on Plugged Media.

.. see also:: :class:`galaxy.model.PluggedMedia`
"""
import logging

from galaxy import exceptions
from galaxy import web
from galaxy.managers import (
    datasets,
    hdas,
    plugged_media,
    users
)
from galaxy.util import (
    string_as_bool
)
from galaxy.web import (
    _future_expose_api as expose_api)

from galaxy.web.base.controller import BaseAPIController

log = logging.getLogger(__name__)


class PluggedMediaController(BaseAPIController):
    """
    RESTful controller for interactions with plugged media.
    """

    def __init__(self, app):
        super(PluggedMediaController, self).__init__(app)
        self.user_manager = users.UserManager(app)
        self.plugged_media_manager = plugged_media.PluggedMediaManager(app)
        self.plugged_media_serializer = plugged_media.PluggedMediaSerializer(app)
        self.hda_manager = hdas.HDAManager(app)
        self.dataset_manager = datasets.DatasetManager(app)

    @web.expose_api_anonymous
    def index(self, trans, **kwd):
        """
        GET /api/plugged_media: returns a list of installed plugged media
        """
        user = self.user_manager.current_user(trans)
        if self.user_manager.is_anonymous(user):
            # an anonymous user is not expected to have installed a plugged media.
            return []
        rtv = []
        for pm in user.plugged_media:
            rtv.append(self.plugged_media_serializer.serialize_to_view(
                pm, user=trans.user, trans=trans, **self._parse_serialization_params(kwd, 'summary')))
        return rtv

    @web.expose_api_anonymous
    def create(self, trans, payload, **kwd):
        """
        create(self, trans, payload, **kwd)
        * POST /api/plugged_media:
            Creates a new plugged media.

        :type  trans: galaxy.web.framework.webapp.GalaxyWebTransaction
        :param trans: Galaxy web transaction.

        :type  payload: dict
        :param payload: A dictionary structure containing the following keys:
            - hierarchy: A key which defines the hierarchical relation between this and other plugged media defined
            by the user.
            - category: is the type of this plugged media, its value is a key from `categories` bunch defined in the
            `PluggedMedia` class.
            - path: a path in the plugged media to be used (e.g., AWS S3 Bucket name).
            - credentials (Optional): It is a JSON object containing required credentials to access the plugged media
             (e.g., access and secret key for an AWS S3 bucket).

        :rtype: dict
        :return: The newly created plugged media.
        """
        if not isinstance(payload, dict):
            trans.response.status = 400
            return "Invalid payload data type. The payload is expected to be a dictionary," \
                   " but received data of type '%s'." % str(type(payload))

        missing_arguments = []
        hierarchy = payload.get("hierarchy")
        if hierarchy is None:
            missing_arguments.append("hierarchy")
        category = payload.get("category")
        if category is None:
            missing_arguments.append("category")
        path = payload.get("path")
        if path is None:
            missing_arguments.append("path")
        if len(missing_arguments) > 0:
            trans.response.status = 400
            return "The following required arguments are missing in the payload: %s" % missing_arguments
        purgeable = string_as_bool(payload.get("purgeable", True))

        try:
            new_plugged_media = self.plugged_media_manager.create(
                user_id=trans.user.id,
                hierarchy=hierarchy,
                category=category,
                path=path,
                credentials=payload.get("credentials", None),
                purgeable=purgeable)
            view = self.plugged_media_serializer.serialize_to_view(
                new_plugged_media, user=trans.user, trans=trans, **self._parse_serialization_params(kwd, 'summary'))
            # Do not use integer response codes (e.g., 200), as they are not accepted by the
            # 'wsgi_status' function in lib/galaxy/web/framework/base.py
            trans.response.status = '200 OK'
            log.debug('Created a new plugged media of type `%s` for the user id `%s` ', category, str(trans.user.id))
            return view
        except Exception as e:
            log.exception('An unexpected error has occurred while responding to the '
                          'create request of the plugged media API. ' + str(e))
            # Do not use integer response code (see above).
            trans.response.status = '500 Internal Server Error'
            return []

    @expose_api
    def delete(self, trans, id, **kwd):
        """
        delete(self, trans, id, **kwd)
        * DELETE /api/plugged_media/{id}
            Deletes the plugged media with the given ID, also deletes all the associated datasets and HDAs.

        :type  trans: galaxy.web.framework.webapp.GalaxyWebTransaction
        :param trans: Galaxy web transaction.

        :type id: string
        :param id: The encoded ID of the plugged media to be deleted.

        :type kwd: dict
        :param kwd: (optional) dictionary structure containing extra parameters (e.g., `purge`).

        :rtype: dict
        :return: The deleted or purged plugged media.
        """
        try:
            plugged_media = self.plugged_media_manager.get_owned(self.decode_id(id), trans.user)
            payload = kwd.get('payload', None)
            purge = False if payload is None else string_as_bool(payload.get('purge', False))
            if purge:
                self.plugged_media_manager.purge(plugged_media)
            else:
                self.plugged_media_manager.delete(plugged_media)
            return self.plugged_media_serializer.serialize_to_view(
                plugged_media, user=trans.user, trans=trans, **self._parse_serialization_params(kwd, 'summary'))
        except exceptions.ObjectNotFound:
            raise exceptions.ObjectNotFound('The plugged media with ID `{}` does not exist.'.format(str(id)))
        except exceptions.ConfigDoesNotAllowException as e:
            raise exceptions.ConfigDoesNotAllowException(str(e))
        except AttributeError as e:
            raise AttributeError('An unexpected error has occurred while deleting/purging a plugged media in response '
                                 'to the related API call. Maybe an inappropriate database manipulation. ' + str(e))
        except Exception as e:
            raise Exception('An unexpected error has occurred while deleting/purging a plugged media in response to '
                            'the related API call. ' + str(e))
