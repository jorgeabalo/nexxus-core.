import json
import httpx
import pytest
from car.copilot import OpenAICopilot,CopilotUnavailable

def test_context_memory_privacy_and_account_isolation():
    requests=[]
    def provider(req):
        body=json.loads(req.content);requests.append(body)
        assert body['store'] is False
        assert 'private-owner' not in req.content.decode()
        assert '12.3456' not in req.content.decode()
        return httpx.Response(200,json={'output':[{'type':'message','content':[{'type':'output_text','text':'Comprueba la presión indicada en tu manual.'}]}]})
    ai=OpenAICopilot('test',transport=httpx.MockTransport(provider))
    ctx={'tenant':'a','vehicle':{'id':'v','make':'Kia','model':'Rio','year':2023,'owner':'private-owner'},'parking':{'lat':12.3456},'expense_total':40}
    ai.answer('¿Cómo ahorro gasolina?',ctx)
    ai.answer('¿Y los neumáticos?',ctx)
    assert len(requests[1]['input'])==4
    ai.answer('¿Y los neumáticos?',{**ctx,'tenant':'b'})
    assert len(requests[2]['input'])==2
    ai.answer('¿Y los neumáticos?',{**ctx,'vehicle':{**ctx['vehicle'],'id':'other'}})
    assert len(requests[3]['input'])==2

def test_failures_and_pilot_limit_fail_closed():
    ai=OpenAICopilot('test',transport=httpx.MockTransport(lambda req:httpx.Response(429)))
    ctx={'tenant':'a','vehicle':{'id':'v'}}
    with pytest.raises(CopilotUnavailable):ai.answer('hola',ctx)
    with pytest.raises(CopilotUnavailable):ai.answer('hola',{'vehicle':{'id':'v'}})
