from __future__ import unicode_literals

from temba.msgs.handler import MessageHandler
from .models import Flow


class FlowHandler(MessageHandler):
    def __init__(self):
        super(FlowHandler, self).__init__('rules')

    def handle(self, msg):
        # hand off to our Flow object to handle
        return Flow.find_and_handle(msg)
