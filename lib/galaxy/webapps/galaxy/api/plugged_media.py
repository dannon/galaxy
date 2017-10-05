"""
API operations on Plugged Media.

.. see also:: :class:`galaxy.model.PluggedMedia`
"""
import logging

from galaxy import web
from galaxy.managers import (
    users,
    plugged_media
)
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


    @web.expose_api_anonymous
    def index(self, trans, **kwd):
        """
        GET /api/plugged_media: returns a list of installed plugged media
        """
        user = self.user_manager.current_user(trans)
        if self.user_manager.is_anonymous(user):
            # an anonymous user is not expected to have installed a plugged media.
            return []
        # TODO: iterate through the plugged media and show its content manually avoiding secret and access key
        return user.plugged_media
