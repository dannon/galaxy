from galaxy.app import UniverseApplication
from .config import Configuration


class ReportsUniverseApplication( UniverseApplication ):
    """
    The state of a reports-specific universe application
    """
    name = "reports"

    def __init__( self, **kwargs ):
        super(ReportsUniverseApplication, self).__init__(**kwargs)

    def __init_config( self, **kwargs ):
        self.config = Configuration( **kwargs )
