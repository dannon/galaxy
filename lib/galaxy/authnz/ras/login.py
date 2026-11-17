"""A user's direct RAS login, driven over the API instead of the smoke script.

RAS only redirects to the client's registered callback, which Galaxy usually
can't receive, so the user pastes the URL they landed on back to Galaxy. The
state and nonce of the pending attempt live in the user's vault between the two
steps.
"""

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from galaxy.authnz.ras.models import Passport
from galaxy.authnz.ras.sources import (
    DirectRasPassportSource,
    parse_callback,
    PassportSourceError,
)
from galaxy.authnz.ras.store import PassportStore
from galaxy.authnz.ras.verify import PassportVerificationError
from galaxy.security.vault import (
    UserVaultWrapper,
    Vault,
)

log = logging.getLogger(__name__)

LOGIN_REQUEST_VAULT_KEY = "ras/login_request"
LOGIN_REQUEST_LIFETIME = 600  # seconds


class PassportLoginError(Exception):
    pass


class DirectRasLogin:
    def __init__(
        self,
        source: DirectRasPassportSource,
        store: PassportStore,
        vault: Vault,
        clock: Callable[[], float] = time.time,
    ):
        self.source = source
        self.store = store
        self.vault = vault
        self.clock = clock

    def start(self, user: Any) -> str:
        """The RAS authorization URL; replaces any login the user had pending."""
        try:
            request = self.source.new_authorization_request()
        except PassportSourceError as e:
            raise PassportLoginError(str(e)) from e
        saved = {"state": request.state, "nonce": request.nonce, "created": self.clock()}
        UserVaultWrapper(self.vault, user).write_secret(LOGIN_REQUEST_VAULT_KEY, json.dumps(saved))
        return request.url

    def finish(self, user: Any, callback_url: str) -> Passport:
        """Redeem the pasted callback URL and store the verified passport."""
        user_vault = UserVaultWrapper(self.vault, user)
        raw_saved = user_vault.read_secret(LOGIN_REQUEST_VAULT_KEY)
        # Consume it first: a failed attempt must not be retryable with the same state.
        user_vault.delete_secret(LOGIN_REQUEST_VAULT_KEY)
        if not raw_saved:
            raise PassportLoginError("No RAS login is in progress; start a new one.")
        saved = json.loads(raw_saved)
        if self.clock() - saved["created"] > LOGIN_REQUEST_LIFETIME:
            raise PassportLoginError("The RAS login timed out; start a new one.")
        try:
            callback = parse_callback(callback_url)
            if "state" not in callback:
                raise PassportLoginError("The pasted URL has no state; paste the full URL RAS redirected to.")
            handoff = dict(callback, expected_state=saved["state"], nonce=saved["nonce"])
            raw_passport = self.source.obtain(user, handoff)
            return self.store.put(user, raw_passport)
        except (PassportSourceError, PassportVerificationError) as e:
            raise PassportLoginError(str(e)) from e
