# Python 2/3 adapter, used only during a normal authorized game launch.
init 999 python:
    def _stream_apply_saved_edits():
        import json
        import math
        import renpy
        import sys
        if sys.version_info[0] == 2:
            import __builtin__ as primitive
        else:
            import builtins as primitive
        log = renpy.game.log
        payload = vars(log).pop('_vn_stream_edits', None)
        if not isinstance(payload, primitive.basestring if renpy.version_tuple[0] < 8 else primitive.str):
            return
        if len(payload) > 16 * 1024 * 1024:
            return
        try:
            changes = json.loads(payload)
            if not type(changes) is primitive.dict or len(changes) > 10000:
                return
            for key, value in changes.items():
                if not key.startswith('store.') or '.' not in key:
                    continue
                store_name, name = key.rsplit('.', 1)
                if name.startswith('_'):
                    continue
                store = renpy.python.store_dicts.get(store_name, {})
                original = store.get(name)
                try:
                    text_types = (primitive.basestring,)
                    integer_types = (primitive.int, primitive.long)
                except AttributeError:
                    text_types = (primitive.str,)
                    integer_types = (primitive.int,)
                valid = ((type(original) is primitive.bool and type(value) is primitive.bool)
                         or (isinstance(original, text_types) and isinstance(value, text_types))
                         or (type(original) in integer_types and type(value) in integer_types)
                         or (type(original) is primitive.float and type(value) is primitive.float
                             and not math.isnan(value) and not math.isinf(value)))
                if valid:
                    store[name] = value
        except (ValueError, TypeError, KeyError, OverflowError):
            return
    config.after_load_callbacks.insert(0, _stream_apply_saved_edits)
