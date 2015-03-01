"""
Base UniverseApplication shared by all webapps
"""

import sys
import time

import galaxy.model
from .config import Configuration
from galaxy.config import configure_logging
from galaxy.web import security

app = None


class UniverseApplication( object ):
    """Encapsulates the state of a base Universe application"""

    name = "base_application"

    def __init__( self, **kwargs ):
        print >> sys.stderr, "python path is: " + ", ".join( sys.path )
        self.new_installation = False
        self.trace_logger = None
        # Read config file and check for errors
        self.__init_config(**kwargs)
        self.config.check()
        configure_logging( self.config )
        # Determine the database url
        if self.config.database_connection:
            db_url = self.config.database_connection
        else:
            db_url = "sqlite:///%s?isolation_level=IMMEDIATE" % self.config.database
        self.__setup_model(db_url)
        if not self.config.database_connection:
            self.targets_mysql = False
        else:
            self.targets_mysql = 'mysql' in self.config.database_connection
        # Security helper
        self.security = security.SecurityHelper( id_secret=self.config.id_secret )
        # used for cachebusting
        self.server_starttime = int(time.time())

    def __setup_model( self, db_url):
        # Setup the database engine and ORM
        self.model = galaxy.model.mapping.init( self.config.file_path,
                                                db_url,
                                                self.config.database_engine_options,
                                                create_tables=True )

    def __init_config( self, **kwargs ):
        self.config = Configuration( **kwargs )

    def shutdown( self ):
        pass
