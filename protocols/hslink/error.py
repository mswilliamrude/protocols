class HSLinkError(Exception): pass
class TimeoutError(HSLinkError): pass
class CancelError(HSLinkError): pass
class ProtocolError(HSLinkError): pass
class CRCError(ProtocolError): pass
