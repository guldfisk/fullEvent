from pydispatch import dispatcher as dp
import copy

def dictMerge(*dicts):
	result = {}
	for dictionary in dicts: result.update(dictionary)
	return result

def removeKey(d, key):
    r = dict(d)
    del r[key]
    return r
	
class EventSession(object):
	def __init__(self, **kwargs):
		self.dp = dp.Dispatcher()
		self.replaceOrder = replaceOrder
		self.orderTriggers = orderTriggers
		self.orderedTimeStamps = orderedTimeStamps
		self.triggerQueue = []
		self.time = -1
		self.conditions = []
		self.eventCleanup = None
		self.stack = []
	def getTimeStamp(self, **kwargs):
		self.time += 1
		return self.time
	def resolveEvent(self, event, **kwargs):
		return event(session = self, **kwargs).resolve()
	def connectCondition(self, tp, **kwargs):
		o = tp(self, **kwargs)
		self.conditions.append(o)
		o.connect()
		return o
	def disconnectCondition(self, condition, **kwargs):
		if not condition in self.conditions: return
		condition.disconnect()
		self.conditions.remove(condition)
	def spawnCondition(self, con, **kwargs):
		c = con.spawnClone(**kwargs)
		self.conditions.append(c)
		c.connect()
		return c
	def resolveTriggerQueue(self, **kwargs):
		if not (self.triggerQueue or self.stack): return
		self.stack.extend(self.orderTriggers(self.triggerQueue))
		self.triggerQueue[:] = []
		while self.stack:
			trigger = self.stack.pop()
			trigger[0].resolve(**trigger[1])
		
def replaceOrder(options):
	return 0
	
def orderTriggers(options):
	return options

def orderedTimeStamps(options):
	return sorted(options, key=lambda x: x.timeStamp)
	
class SessionSub(object):
	name = 'BaseSessionSub'
	def __init__(self, **kwargs):
		self.session = kwargs.pop('session', None)
		self.source = kwargs.pop('source', None)
	def spawn(self, tp, **kwargs):
		return tp(**dictMerge(self.__dict__, kwargs))
	def spawnClone(self, **kwargs):
		return type(self)(**dictMerge(self.__dict__, kwargs))
	
class Condition(SessionSub):
	name = 'BaseCondition'
	defaultTrigger = ''
	def __init__(self, session, **kwargs):
		self.session = session
		self.source = kwargs.pop('source', None)
		self.trigger = kwargs.pop('trigger', self.defaultTrigger)
		self.successfulLoad = kwargs.pop('successfulLoad', self.successfulLoad)
		self.condition = kwargs.pop('condition', self.condition)
		self.resolve = kwargs.pop('resolve', self.resolve)
		self.timeStamp = session.getTimeStamp()
		self.__dict__.update(kwargs)
	def getTrigger(self, **kwargs):
		return self.trigger
	def condition(self, **kwargs):
		return True
	def load(self, **kwargs):
		if self.condition(**kwargs): return self.successfulLoad(**kwargs)
	def successfulLoad(self, **kwargs):
		pass
	def resolve(self, **kwargs):
		pass
	def connect(self, **kwargs):
		self.session.dp.connect(self.load, signal=self.getTrigger())
	def disconnect(self, **kwargs):
		self.session.dp.disconnect(self.load, signal=self.getTrigger())	
		
class Event(SessionSub):
	name = 'BaseEvent'
	def __init__(self, **kwargs):
		super(Event, self).__init__(**kwargs)
		self.hasReplaced = copy.copy(kwargs.pop('hasReplaced', []))
		self.source = kwargs.pop('source', None)
		self.__dict__.update(kwargs)
	def payload(self, **kwargs):
		pass
	def setup(self, **kwargs):
		pass
	def check(self, **kwargs):
		pass
	def resolve(self, **kwargs):
		if self.setup(**kwargs): return
		replacements = [replacement[1] for replacement in self.session.dp.send(signal='try_'+self.name, **self.__dict__) if not replacement[1] in self.hasReplaced]
		if not replacements:
			if self.check(**kwargs): return
			self.session.dp.send(self.name+'_begin', **self.__dict__)
			result = self.payload(**kwargs)
			self.session.dp.send(self.name, **self.__dict__)
			if self.session.eventCleanup: self.session.eventCleanup()
			return result
		choice = self.session.replaceOrder(replacements)
		self.hasReplaced.append(replacements[choice])
		return replacements[choice].resolve(self)
	def spawnTree(self, tp, **kwargs):
		return tp(**removeKey(dictMerge(self.__dict__, kwargs), 'hasReplaced'))

class Replacement(Condition):
	name = 'BaseReplacement'
	def getTrigger(self, **kwargs):
		return 'try_'+self.trigger
	def successfulLoad(self, **kwargs):
		return self
	def resolve(self, event, **kwargs):
		return

class Triggered(Event):
	name = 'BaseTriggered'
	def __init__(self, **kwargs):
		super(Triggered, self).__init__(**kwargs)
		self.trigger = kwargs.get('trigger', None)
	def payload(self, **kwargs):
		self.session.triggerQueue.append((self.trigger, self.circumstance))

class Trigger(Condition):
	name = 'BaseTrigger'
	def successfulLoad(self, **kwargs):
		self.session.resolveEvent(Triggered, trigger=self, circumstance=kwargs)
		
class Continuous(Condition):
	name = 'BaseContinuous'
	defaultTerminatorTrigger = ''
	def __init__(self, session, **kwargs):
		super(Continuous, self).__init__(session, **kwargs)
		self.terminateTrigger = kwargs.get('terminateTrigger', self.defaultTerminatorTrigger)
		self.terminatorCondition = kwargs.get('terminatorCondition', self.condition)
		self.timeStamp = -1
	def getTerminateTrigger(self, **kwargs):
		return self.terminateTrigger
	def terminateCondition(self, **kwargs):
		return True
	def terminate(self, **kwargs):
		if self.terminateCondition(**kwargs): self.disconnect(**kwargs)
	def connect(self, **kwargs):
		super(Continuous, self).connect(**kwargs)
		self.timeStamp = self.session.getTimeStamp()
		self.session.dp.connect(self.terminate, signal=self.getTerminateTrigger())
	def disconnect(self, **kwargs):
		super(Continuous, self).disconnect(**kwargs)
		self.session.dp.disconnect(self.terminate, signal=self.getTerminateTrigger())	
		
class DelayedTrigger(Continuous, Trigger):
	name = 'BaseDelayedTrigger'
	def successfulLoad(self, **kwargs):
		super(DelayedTrigger, self).successfulLoad(**kwargs)
		self.disconnect(**kwargs)
	def terminateCondition(self, **kwargs):
		return False
		
class DelayedReplacement(Continuous, Replacement):
	name = 'BaseDelayedReplacement'
	def successfulLoad(self, **kwargs):
		self.disconnect(**kwargs)
		return self
	def terminateCondition(self, **kwargs):
		return False
	
#Add-on
class AttributeModifying(object):
	def getTrigger(self, **kwargs):
		return 'AccessAttribute_'+self.trigger
	def resolve(self, val, **kwargs):
		return val
	def successfulLoad(self, **kwargs):
		return self
		
class ADStatic(AttributeModifying, Condition):
	name = 'BaseADStatic'
	
class ADContinuous(AttributeModifying, Continuous):
	name = 'BaseADCountinuous'
	
class ProtectedAttribute(object):
	def __init__(self, master, name, val, **kwargs):
		self.master = master
		self.name = name
		self.val = val
	def access(self, **kwargs):
		val = copy.copy(self.val)
		for respons in self.master.session.orderedTimeStamps([o[1] for o in self.master.session.dp.send('AccessAttribute_'+self.name, master=self.master, val=val, **kwargs)]):
			if respons!=None: val = respons.resolve(val, **kwargs)
		return val
	def set(self, val, **kwargs):
		self.val = val
		
class WithPAs(object):
	def PA(self, name, val, **kwargs):
		return ProtectedAttribute(self, name, val, **kwargs)
		
#-----------------------------------------------------------------------
		
class TestEvent(Event):
	name = 'TestEvent'
	def payload(self, **kwargs):
		print(self.name+' payload from '+str(id(self)))

class TestEvent2(TestEvent):
	name = 'TestEvent2'
		
class TestReplacement(Replacement):
	name = 'TestReplacement'
	defaultTrigger = 'TestEvent'
	def resolve(self, event, **kwargs):
		for i in range(2): event.spawnClone().resolve()
	
class TestReplacement2(Replacement):
	name = 'TestReplacement2'
	defaultTrigger = 'TestEvent2'
	def resolve(self, event, **kwargs):
		return
	
class TestTrigger(Trigger):
	name = 'TestTrigger'
	defaultTrigger = 'TestEvent'
	def resolve(self, **kwargs):
		self.session.resolveEvent(TestEvent2)
	
def evLogger(signal, **kwargs):
	print('>'+signal+':: '+str(list(kwargs)))
	
if __name__=='__main__':
	ses = EventSession()
	#ses.dp.connect(evLogger)
	#tr = TestReplacement(ses)
	#tr.connect()
	ses.connectCondition(TestReplacement)
	#ses.connectCondition(TestReplacement)
	#tr2 = TestReplacement2(ses)
	#tr2.connect()
	
	ses.connectCondition(TestTrigger)
	
	ses.resolveEvent(TestEvent)
	#ses.resolveEvent(TestEvent)
	
	ses.resolveTriggerQueue()
	#ses.resolveTriggerQueue()
