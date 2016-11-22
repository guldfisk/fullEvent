#FORKED AS FUCK

"""Multiple-producer-multiple-consumer signal-dispatching

dispatcher is the core of the PyDispatcher system,
providing the primary API and the core logic for the
system.

Module attributes of note:

	Any -- Singleton used to signal either "Any Sender" or
		"Any Signal".  See documentation of the _Any class.
	Anonymous -- Singleton used to signal "Anonymous Sender"
		See documentation of the _Anonymous class.

Internal attributes:
	WEAKREF_TYPES -- tuple of types/classes which represent
		weak references to receivers, and thus must be de-
		referenced on retrieval to retrieve the callable
		object
	connections -- { senderkey (id) : { signal : [receivers...]}}
	senders -- { senderkey (id) : weakref(sender) }
		used for cleaning up sender references on sender
		deletion
	sendersBack -- { receiverkey (id) : [senderkey (id)...] }
		used for cleaning up receiver references on receiver
		deletion, (considerably speeds up the cleanup process
		vs. the original code.)
"""
import weakref
from pydispatch import saferef, robustapply, errors
import copy

class _Parameter:
	"""Used to represent default parameter values."""
	def __repr__(self):
		return self.__class__.__name__

class _Any(_Parameter):
	"""Singleton used to signal either "Any Sender" or "Any Signal"

	The Any object can be used with connect, disconnect,
	send, or sendExact to signal that the parameter given
	Any should react to all senders/signals, not just
	a particular sender/signal.
	"""
Any = _Any()

class _Anonymous(_Parameter):
	"""Singleton used to signal "Anonymous Sender"

	The Anonymous object is used to signal that the sender
	of a message is not specified (as distinct from being
	"any sender").  Registering callbacks for Anonymous
	will only receive messages sent without senders.  Sending
	with anonymous will only send messages to those receivers
	registered for Any or Anonymous.

	Note:
		The default sender for connect is Any, while the
		default sender for send is Anonymous.  This has
		the effect that if you do not specify any senders
		in either function then all messages are routed
		as though there was a single sender (Anonymous)
		being used everywhere.
	"""
Anonymous = _Anonymous()

WEAKREF_TYPES = (weakref.ReferenceType, saferef.BoundMethodWeakref)

class Dispatcher(object):
	def __init__(self):
		self.connections = {}
		self.senders = {}
		self.sendersBack = {}
	def connect(self, receiver, signal=Any, sender=Any, weak=True):
		"""Connect receiver to sender for signal

		receiver -- a callable Python object which is to receive
			messages/signals/events.  Receivers must be hashable
			objects.

			if weak is True, then receiver must be weak-referencable
			(more precisely saferef.safeRef() must be able to create
			a reference to the receiver).
		
			Receivers are fairly flexible in their specification,
			as the machinery in the robustApply module takes care
			of most of the details regarding figuring out appropriate
			subsets of the sent arguments to apply to a given
			receiver.

			Note:
				if receiver is itself a weak reference (a callable),
				it will be de-referenced by the system's machinery,
				so *generally* weak references are not suitable as
				receivers, though some use might be found for the
				facility whereby a higher-level library passes in
				pre-weakrefed receiver references.

		signal -- the signal to which the receiver should respond
		
			if Any, receiver will receive any signal from the
			indicated sender (which might also be Any, but is not
			necessarily Any).
			
			Otherwise must be a hashable Python object other than
			None (DispatcherError raised on None).
			
		sender -- the sender to which the receiver should respond
		
			if Any, receiver will receive the indicated signals
			from any sender.
			
			if Anonymous, receiver will only receive indicated
			signals from send/sendExact which do not specify a
			sender, or specify Anonymous explicitly as the sender.

			Otherwise can be any python object.
			
		weak -- whether to use weak references to the receiver
			By default, the module will attempt to use weak
			references to the receiver objects.  If this parameter
			is false, then strong references will be used.

		returns None, may raise DispatcherTypeError
		"""
		if signal is None:
			raise errors.DispatcherTypeError(
				'Signal cannot be None (receiver=%r sender=%r)'%( receiver,sender)
			)
		if weak:
			receiver = saferef.safeRef(receiver, onDelete=self._removeReceiver)
		senderkey = id(sender)
		if senderkey in self.connections:
			signals = self.connections[senderkey]
		else:
			self.connections[senderkey] = signals = {}
		# Keep track of self.senders for cleanup.
		# Is Anonymous something we want to clean up?
		if sender not in (None, Anonymous, Any):
			def remove(object, senderkey=senderkey):
				_removeSender(senderkey=senderkey)
			# Skip objects that can not be weakly referenced, which means
			# they won't be automatically cleaned up, but that's too bad.
			try:
				weakSender = weakref.ref(sender, remove)
				self.senders[senderkey] = weakSender
			except:
				pass
			
		receiverID = id(receiver)
		# get current set, remove any current references to
		# this receiver in the set, including back-references
		if signal in signals:
			receivers = signals[signal]
			self._removeOldBackRefs(senderkey, signal, receiver, receivers)
		else:
			receivers = signals[signal] = []
		try:
			current = self.sendersBack.get( receiverID )
			if current is None:
				self.sendersBack[ receiverID ] = current = []
			if senderkey not in current:
				current.append(senderkey)
		except:
			pass

		receivers.append(receiver)
	def disconnect(self, receiver, signal=Any, sender=Any, weak=True):
		"""Disconnect receiver from sender for signal

		receiver -- the registered receiver to disconnect
		signal -- the registered signal to disconnect
		sender -- the registered sender to disconnect
		weak -- the weakref state to disconnect

		disconnect reverses the process of connect,
		the semantics for the individual elements are
		logically equivalent to a tuple of
		(receiver, signal, sender, weak) used as a key
		to be deleted from the internal routing tables.
		(The actual process is slightly more complex
		but the semantics are basically the same).

		Note:
			Using disconnect is not required to cleanup
			routing when an object is deleted, the framework
			will remove routes for deleted objects
			automatically.  It's only necessary to disconnect
			if you want to stop routing to a live object.
			
		returns None, may raise DispatcherTypeError or
			DispatcherKeyError
		"""
		if signal is None:
			raise errors.DispatcherTypeError(
				'Signal cannot be None (receiver=%r sender=%r)'%( receiver,sender)
			)
		if weak: receiver = saferef.safeRef(receiver)
		senderkey = id(sender)
		try:
			signals = self.connections[senderkey]
			receivers = signals[signal]
		except KeyError:
			raise errors.DispatcherKeyError(
				"""No receivers found for signal %r from sender %r""" %(
					signal,
					sender
				)
			)
		try:
			# also removes from receivers
			self._removeOldBackRefs(senderkey, signal, receiver, receivers)
		except ValueError:
			raise errors.DispatcherKeyError(
				"""No connection to receiver %s for signal %s from sender %s""" %(
					receiver,
					signal,
					sender
				)
			)
		self._cleanupConnections(senderkey, signal)
	def getReceivers(self, sender = Any, signal = Any ):
		"""Get list of receivers from global tables

		This utility function allows you to retrieve the
		raw list of receivers from the self.connections table
		for the given sender and signal pair.

		Note:
			there is no guarantee that this is the actual list
			stored in the self.connections table, so the value
			should be treated as a simple iterable/truth value
			rather than, for instance a list to which you
			might append new records.

		Normally you would use liveReceivers( getReceivers( ...))
		to retrieve the actual receiver objects as an iterable
		object.
		"""
		try:
			return self.connections[id(sender)][signal]
		except KeyError:
			return []
	def liveReceivers(self, receivers):
		"""Filter sequence of receivers to get resolved, live receivers

		This is a generator which will iterate over
		the passed sequence, checking for weak references
		and resolving them, then returning all live
		receivers.
		"""
		retur = []
		for receiver in receivers:
			if isinstance( receiver, WEAKREF_TYPES):
				# Dereference the weak reference.
				receiver = receiver()
				if receiver is not None:
					retur.append(receiver)
			else:
				retur.append(receiver)
		return retur
	def getAllReceivers(self, sender = Any, signal = Any ):
		"""Get list of all receivers from global tables

		This gets all receivers which should receive
		the given signal from sender, each receiver should
		be produced only once by the resulting generator
		"""
		receivers = {}
		for set in (
			# Get receivers that receive *this* signal from *this* sender.
			self.getReceivers( sender, signal ),
			# Add receivers that receive *any* signal from *this* sender.
			self.getReceivers( sender, Any ),
			# Add receivers that receive *this* signal from *any* sender.
			self.getReceivers( Any, signal ),
			# Add receivers that receive *any* signal from *any* sender.
			self.getReceivers( Any, Any ),
		):
			for receiver in set:
				if receiver: # filter out dead instance-method weakrefs
					try:
						if receiver not in receivers:
							receivers[receiver] = 1
							yield receiver
					except TypeError:
						# dead weakrefs raise TypeError on hash...
						pass
	def send(self, signal=Any, sender=Anonymous, *arguments, **named):
		"""Send signal from sender to all connected receivers.
		
		signal -- (hashable) signal value, see connect for details

		sender -- the sender of the signal
		
			if Any, only receivers registered for Any will receive
			the message.

			if Anonymous, only receivers registered to receive
			messages from Anonymous or Any will receive the message

			Otherwise can be any python object (normally one
			registered with a connect if you actually want
			something to occur).

		arguments -- positional arguments which will be passed to
			*all* receivers. Note that this may raise TypeErrors
			if the receivers do not allow the particular arguments.
			Note also that arguments are applied before named
			arguments, so they should be used with care.

		named -- named arguments which will be filtered according
			to the parameters of the receivers to only provide those
			acceptable to the receiver.

		Return a list of tuple pairs [(receiver, response), ... ]

		if any receiver raises an error, the error propagates back
		through send, terminating the dispatch loop, so it is quite
		possible to not have all receivers called if a raises an
		error.
		"""
		# Call each receiver with whatever arguments it can accept.
		# Return a list of tuple pairs [(receiver, response), ... ].
		responses = []
		for receiver in self.liveReceivers(self.getAllReceivers(sender, signal)):
			response = robustapply.robustApply(
				receiver,
				signal=signal,
				sender=sender,
				*arguments,
				**named
			)
			if response:
				responses.append((receiver, response))
		return responses
	def sendExact(self, signal=Any, sender=Anonymous, *arguments, **named ):
		"""Send signal only to those receivers registered for exact message

		sendExact allows for avoiding Any/Anonymous registered
		handlers, sending only to those receivers explicitly
		registered for a particular signal on a particular
		sender.
		"""
		responses = []
		for receiver in self.liveReceivers(self.getReceivers(sender, signal)):
			response = robustapply.robustApply(
				receiver,
				signal=signal,
				sender=sender,
				*arguments,
				**named
			)
			responses.append((receiver, response))
		return responses
	def _removeReceiver(self, receiver):
		"""Remove receiver from self.connections."""
		if not self.sendersBack:
			# During module cleanup the mapping will be replaced with None
			return False
		backKey = id(receiver)
		try:
			backSet = self.sendersBack.pop(backKey)
		except KeyError:
			return False 
		else:
			for senderkey in backSet:
				try:
					signals = list(self.connections[senderkey].keys())
				except KeyError:
					pass
				else:
					for signal in signals:
						try:
							receivers = self.connections[senderkey][signal]
						except KeyError:
							pass
						else:
							try:
								receivers.remove( receiver )
							except Exception:
								pass
						self._cleanupConnections(senderkey, signal)
	def _cleanupConnections(self, senderkey, signal):
		"""Delete any empty signals for senderkey. Delete senderkey if empty."""
		try:
			receivers = self.connections[senderkey][signal]
		except:
			pass
		else:
			if not receivers:
				# No more connected receivers. Therefore, remove the signal.
				try:
					signals = self.connections[senderkey]
				except KeyError:
					pass
				else:
					del signals[signal]
					if not signals:
						# No more signal self.connections. Therefore, remove the sender.
						self._removeSender(senderkey)
	def _removeSender(self, senderkey):
		"""Remove senderkey from self.connections."""
		self._removeBackrefs(senderkey)
		try:
			del self.connections[senderkey]
		except KeyError:
			pass
		# Senderkey will only be in self.senders dictionary if sender 
		# could be weakly referenced.
		try: 
			del self.senders[senderkey]
		except: 
			pass
	def _removeBackrefs(self, senderkey):
		"""Remove all back-references to this senderkey"""
		try:
			signals = self.connections[senderkey]
		except KeyError:
			signals = None
		else:
			items = signals.items()
			def allReceivers( ):
				for signal,set in items:
					for item in set:
						yield item
			for receiver in allReceivers():
				self._killBackref( receiver, senderkey )
	def _removeOldBackRefs(self, senderkey, signal, receiver, receivers):
		"""Kill old self.sendersBack references from receiver

		This guards against multiple registration of the same
		receiver for a given signal and sender leaking memory
		as old back reference records build up.

		Also removes old receiver instance from receivers
		"""
		try:
			index = receivers.index(receiver)
			# need to scan back references here and remove senderkey
		except ValueError:
			return False
		else:
			oldReceiver = receivers[index]
			del receivers[index]
			found = 0
			signals = self.connections.get(signal)
			if signals is not None:
				for sig,recs in self.connections.get(signal,{}).items():
					if sig != signal:
						for rec in recs:
							if rec is oldReceiver:
								found = 1
								break
			if not found:
				self._killBackref( oldReceiver, senderkey )
				return True
			return False					
	def _killBackref(self, receiver, senderkey ):
		"""Do the actual removal of back reference from receiver to senderkey"""
		receiverkey = id(receiver)
		set = self.sendersBack.get( receiverkey, () )
		while senderkey in set:
			try:
				set.remove( senderkey )
			except:
				break
		if not set:
			try:
				del self.sendersBack[ receiverkey ]
			except KeyError:
				pass
		return True
