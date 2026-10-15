"""
Calling Claude for answers that can run long (a whole series of storyboards, an
edit plan, a batch of ideas). These are streamed: the SDK refuses a plain request
whose max_tokens could take longer than its 10-minute limit, and streaming also
keeps a slow answer from timing out. Callers get the same final message object.
"""


def final_message(client, **request):
    with client.beta.messages.stream(**request) as stream:
        return stream.get_final_message()
