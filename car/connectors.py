"""Non-secret, tenant-scoped connector preferences. Configuration is not connectivity."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class ConnectorSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    maps: Literal['google','apple','waze'] = 'apple'
    music: Literal['spotify','pandora','amazon'] = 'spotify'
    obd_name: str = Field(default='',max_length=80)
    projection: Literal['phone','carplay','android_auto','ottocast'] = 'phone'
    assistant: Literal['local','openai'] = 'local'

DEFAULTS = ConnectorSettings().model_dump()
def status(settings):
    return {'maps':'external_app','music':'external_app','obd':'disconnected',
            'projection':'phone' if settings['projection']=='phone' else 'native_integration_required',
            'assistant':'local_context' if settings['assistant']=='local' else 'provider_setup_required'}
