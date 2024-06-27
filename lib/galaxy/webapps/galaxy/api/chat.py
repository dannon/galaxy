"""
API Controller providing Chat functionality
"""
import logging

try:
    import openai
except ImportError:
    openai = None

from galaxy.config import GalaxyAppConfiguration
from galaxy.managers.context import ProvidesUserContext
from galaxy.webapps.galaxy.api import (
    depends,
    DependsOnTrans,
    Router,
)
from galaxy.exceptions import ConfigurationError
from galaxy.schema.schema import ChatPayload

log = logging.getLogger(__name__)

router = Router(tags=["chat"])

PROMPT = """
You are a juestion answering agent, expert on the Galaxy analysis platform and in the fields of computer science, bioinformatics, and genomics.
You will try to answer questions about Galaxy, and if you don't know the answer, you will ask the user to rephrase the question.
"""


@router.cbv
class ChatAPI:
    config: GalaxyAppConfiguration = depends(GalaxyAppConfiguration)

    @router.post("/api/chat")
    def query(self, query: ChatPayload, trans: ProvidesUserContext = DependsOnTrans) -> str:
        """We're off to ask the wizard"""

        if openai is None or self.config.openai_api_key is None:
            raise ConfigurationError("OpenAI is not configured for this instance.")
        else:
            openai.api_key = self.config.openai_api_key

        messages = [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": query.query},
        ]

        if query.context == "tool_error":
            msg = "The user will provide you a Galaxy tool error, and you will try to explain the error and provide a solution."
            messages.append({"role": "system", "content": msg})

        client = OpenAI(
            organization='org-gO14XNCI5fwciPOYeic5Kq14',
            project='$PROJECT_ID',
        )


        response = client.create_chat(
            model="gpt-4o",
            messages=messages,
            temperature=0,
        )
        answer = response["choices"][0]["message"]["content"]
        return answer
