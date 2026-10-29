from car.alexa import CarAlexa
from car.store import Store

def envelope(name,rid='1',token='a'):
    return {'context':{'System':{'user':{'accessToken':token}}},'session':{'sessionId':'s'},'request':{'type':'IntentRequest','requestId':rid,'intent':{'name':name,'slots':{'category':{'value':'gasolina'},'amount':{'value':'40'}}}}}
def test_link_confirmation_and_duplicate_request():
    s=Store(':memory:');s.seed('a');v=s.vehicles('a')[0]
    with s.transaction():p=s.profile('a');p['primary_vehicle_id']=v['id'];s.save_profile('a',p)
    skill=CarAlexa(s,lambda token:'a' if token=='a' else None)
    assert skill.handle(envelope('RecordExpenseIntent',token='invalid'))['response']['card']['type']=='LinkAccount'
    skill.handle(envelope('RecordExpenseIntent'))
    assert s.records('a',v['id'],'expense')==[]
    yes=envelope('AMAZON.YesIntent','2');skill.handle(yes);skill.handle(yes)
    assert len(s.records('a',v['id'],'expense'))==1
    assert s.records('a',v['id'],'expense')[0]['amount']==40
    assert s.records('b',v['id'],'expense')==[]
def test_cancel_does_not_write():
    s=Store(':memory:');s.seed('a');v=s.vehicles('a')[0]
    with s.transaction():p=s.profile('a');p['primary_vehicle_id']=v['id'];s.save_profile('a',p)
    skill=CarAlexa(s,lambda token:'a');skill.handle(envelope('RecordExpenseIntent'))
    skill.handle(envelope('AMAZON.NoIntent','2'));skill.handle(envelope('AMAZON.YesIntent','3'))
    assert s.records('a',v['id'],'expense')==[]

def test_collect_expense_in_steps_and_invalidate_stale_confirmation():
    s=Store(':memory:');s.seed('a');v=s.vehicles('a')[0]
    with s.transaction():p=s.profile('a');p['primary_vehicle_id']=v['id'];s.save_profile('a',p)
    skill=CarAlexa(s,lambda token:'a')
    skill.handle(envelope('RecordExpenseIntent','old'))
    req=envelope('RecordExpenseIntent','start');req['request']['intent']['slots']={}
    answer=skill.handle(req)['response']
    assert answer['directives'][0]['slotToElicit']=='category'
    assert skill.handle(envelope('AMAZON.YesIntent','stale'))['response']['shouldEndSession']
    assert s.records('a',v['id'],'expense')==[]
    req=envelope('RecordExpenseIntent','category');del req['request']['intent']['slots']['amount']
    answer=skill.handle(req)['response']
    assert answer['directives'][0]['slotToElicit']=='amount'
    assert answer['directives'][0]['updatedIntent']['slots']['category']['value']=='gasolina'
    assert answer['reprompt']['outputSpeech']['text']==answer['outputSpeech']['text']
    answer=skill.handle(envelope('RecordExpenseIntent','complete'))['response']
    assert 'Confirmas' in answer['outputSpeech']['text']
    assert s.records('a',v['id'],'expense')==[]
    skill.handle(envelope('AMAZON.YesIntent','confirm'))
    assert s.records('a',v['id'],'expense')[0]['amount']==40
