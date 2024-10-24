"""
API Controller providing Chat functionality
"""
import logging

import openai

from galaxy.config import GalaxyAppConfiguration
from galaxy.exceptions import ConfigurationError
from galaxy.schema.schema import ChatPayload
from . import (
    depends,
    Router,
)

log = logging.getLogger(__name__)

router = Router(tags=["chat"])

PROMPT = """
You are a highly intelligent question answering agent, expert on the Galaxy analysis platform and in the fields of computer science, bioinformatics, and genomics.
If asked a question that you confidently know the answer to, you will give you the answer.
If asked a question that is nonsense, trickery, or has no clear answer, you will respond with "Unknown".
"""

@router.cbv
class ChatAPI:
    config: GalaxyAppConfiguration = depends(GalaxyAppConfiguration)

    @router.post("/api/chat")
    def query(self, query: ChatPayload) -> str:
        """We're off to ask the wizard"""

        if self.config.openai_api_key is None:
            raise ConfigurationError("OpenAI is not configured for this instance.")
        else:
            openai.api_key = self.config.openai_api_key

        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": query.query},
            ],
            temperature=0,
        )

        answer = response['choices'][0]['message']['content']
        return answer
