"""Named order comments and indexed trade identity in the historical v5/v6 profile."""

COMMENTS = ('comment', 'comment_profit', 'comment_loss', 'comment_trailing')
READERS = {group: ('entry_id', 'entry_comment') + (('exit_id', 'exit_comment') if group == 'closedtrades' else ())
           for group in ('opentrades', 'closedtrades')}

def comment_fields(node):
    if '+trade-comments' not in node.overload_id:
        return ()
    return COMMENTS if node.operation.startswith('simulation.exit') else COMMENTS[:1]

def observes_identity(node):
    return bool(comment_fields(node)) or (node.operation == 'trade.read' and node.overload_id.split('.')[-1] in READERS['closedtrades'])
