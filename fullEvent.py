from eventdispatch.dispatcher import DispatchSession
import copy
import itertools
import weakref

def _dict_merge(*dicts):
	result = {}
	for dictionary in dicts:
		result.update(dictionary)
	return result

def _remove_key(d, key):
    r = dict(d)
    del r[key]
    return r
	
class EventSession(object):
	def __init__(self, **kwargs):
		self.dp = DispatchSession()
		self.triggerQueue = []
		self.time = itertools.count(-1, 1)
		self.conditions = set()
		self.stack = []
	def get_time_stamp(self, **kwargs):
		return next(self.time)
	def resolve_event(self, event, **kwargs):
		return event(session = self, **kwargs).resolve()
	def connect_condition(self, cls, **kwargs):
		cls(session = self, **kwargs).connect()
	def resolve_triggers(self, **kwargs):
		if not self.triggerQueue and not self.stack:
			return
		self.stack.extend(self.order_triggers(self.triggerQueue))
		self.triggerQueue[:] = []
		while self.stack:
			self.stack.pop().resolve()
	def choose_replacement(self, options):
		return options[0]
	def order_triggers(self, options):
		return options
	def ordered_time_stamps(self, options):
		return sorted(options, key=lambda x: x.timeStamp)

class SessionSub(object):
	def __init__(self, **kwargs):
		self.session = kwargs.pop('session', None)
		self.source = kwargs.pop('source', None)
	def spawn(self, tp, **kwargs):
		return tp(**_dict_merge(self.__dict__, kwargs))
	def spawn_clone(self, **kwargs):
		return type(self)(**_dict_merge(self.__dict__, kwargs))

class Condition(SessionSub):
	defaultTrigger = ''
	def __init__(self, session, **kwargs):
		super(Condition, self).__init__(session=session, **kwargs)
		self._master = weakref.ref(kwargs.get('master', self.session.conditions))
		self.trigger = kwargs.get('trigger', self.defaultTrigger)
		self.successful_load = kwargs.get('successfulLoad', self.successful_load)
		self.condition = kwargs.get('condition', self.condition)
		self.resolve = kwargs.get('resolve', self.resolve)
		self.timeStamp = -1
	def get_trigger(self, **kwargs):
		return self.trigger
	def condition(self, **kwargs):
		return True
	def load(self, **kwargs):
		if self.condition(**kwargs):
			return self.successful_load(**kwargs)
	def successful_load(self, **kwargs):
		pass
	def resolve(self, **kwargs):
		pass
	def connect(self, **kwargs):
		if self._master() is not None:
			self._master().add(self)
		self.timeStamp = self.session.get_time_stamp()
		self.session.dp.connect(self.load, signal=self.get_trigger())
	def disconnect(self, **kwargs):
		if self._master() is not None:
			self._master().discard(self)
		self.session.dp.disconnect(self.load, signal=self.get_trigger())
		
class EventSetupException(Exception):
	pass
	
class EventCheckException(Exception):
	pass
		
class Event(SessionSub):
	name = 'BaseEvent'
	def __init__(self, **kwargs):
		super(Event, self).__init__(**kwargs)
		self.hasReplaced = copy.copy(kwargs.pop('hasReplaced', []))
		self.__dict__.update(kwargs)
	def payload(self, **kwargs):
		pass
	def setup(self, **kwargs):
		pass
	def check(self, **kwargs):
		pass
	def resolve(self, **kwargs):
		try:
			self.setup(**kwargs)
		except EventSetupException:
			return
		replacements = [
			replacement[1]
			for replacement in
			self.session.dp.send(signal='try_'+self.name, **self.__dict__)
			if not replacement[1] in self.hasReplaced
		]
		if replacements:
			choice = self.session.choose_replacement(replacements)
			self.hasReplaced.append(choice)
			choice.chosen(self)
			return choice.resolve(self)
		try:
			self.check(**kwargs)
		except EventCheckException:
			return
		result = self.payload(**kwargs)
		self.session.dp.send(self.name, **self.__dict__)
		return result	
	def spawn_tree(self, tp, **kwargs):
		return tp(**_remove_key(_dict_merge(self.__dict__, kwargs), 'hasReplaced'))

class Replacement(Condition):
	def get_trigger(self, **kwargs):
		return 'try_'+self.trigger
	def successful_load(self, **kwargs):
		return self
	def chosen(self, event):
		pass

class _TriggerPack(object):
	def __init__(self, trigger, circumstance):
		self.trigger = trigger
		self.circumstance = circumstance
	def resolve(self):
		self.trigger.resolve(**self.circumstance)
		
class Triggered(Event):
	name = 'Triggered'
	def __init__(self, **kwargs):
		super(Triggered, self).__init__(**kwargs)
		self.trigger = kwargs.get('trigger', None)
	def payload(self, **kwargs):
		self.session.triggerQueue.append(
			_TriggerPack(
				self.trigger,
				self.circumstance
			)
		)

class Trigger(Condition):
	def successful_load(self, **kwargs):
		self.session.resolve_event(
			Triggered,
			trigger=self,
			circumstance=kwargs
		)
		
class Continuous(Condition):
	defaultTerminateTrigger = ''
	def __init__(self, session, **kwargs):
		super(Continuous, self).__init__(session, **kwargs)
		self.terminateTrigger = kwargs.get('terminateTrigger', self.defaultTerminateTrigger)
		self.terminate_condition = kwargs.get('terminateCondition', self.terminate_condition)
	def get_terminate_trigger(self, **kwargs):
		return self.terminateTrigger
	def terminate_condition(self, **kwargs):
		return True
	def terminate(self, **kwargs):
		if self.terminate_condition(**kwargs):
			self.disconnect(**kwargs)
	def connect(self, **kwargs):
		super(Continuous, self).connect(**kwargs)
		self.session.dp.connect(self.terminate, signal=self.get_terminate_trigger())
	def disconnect(self, **kwargs):
		super(Continuous, self).disconnect(**kwargs)
		self.session.dp.disconnect(self.terminate, signal=self.get_terminate_trigger())
		
class DelayedTrigger(Continuous, Trigger):
	name = 'BaseDelayedTrigger'
	def successful_load(self, **kwargs):
		super(DelayedTrigger, self).successful_load(**kwargs)
		self.disconnect()
	def terminate_condition(self, **kwargs):
		return False
		
class DelayedReplacement(Continuous, Replacement):
	name = 'BaseDelayedReplacement'
	def chosen(self, event):
		self.disconnect()
	def terminate_condition(self, **kwargs):
		return False
	
#Add-on
class AttributeModifying(object):
	def get_trigger(self, **kwargs):
		return 'AccessAttribute_'+self.trigger
	def resolve(self, val, **kwargs):
		return val
	def successful_load(self, **kwargs):
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
		for response in self.master.session.ordered_time_stamps(
				[
					o[1]
					for o in
					self.master.session.dp.send(
						'AccessAttribute_'+self.name,
						master=self.master,
						val=val,
						**kwargs
					)
				]
		):
			if response is not None:
				val = response.resolve(val, **kwargs)
		return val
	def set(self, val, **kwargs):
		self.val = val
		
class WithPAs(object):
	def pa(self, name, val, **kwargs):
		return ProtectedAttribute(self, name, val, **kwargs)
		
#-----------------------------------------------------------------------
	
def ev_logger(signal, **kwargs):
	print('>'+signal+':: '+str(list(kwargs)))
	
if __name__=='__main__':
	pass
