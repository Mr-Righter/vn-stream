# Python 2/3 service-owned adapter. Loaded only during an authorized game launch.
init 999 python:
    def _stream_actions_init():
        import os
        import json
        import time
        import renpy
        directory = os.environ.get('VN_STREAM_ACTIONS')
        session = os.environ.get('VN_STREAM_SESSION_ID')
        media_path = os.environ.get('VN_STREAM_MEDIA_OWNER')
        if not directory or not session or not media_path:
            return
        # Report only project-owned save locations after normal engine initialization.
        try:
            project = os.path.realpath(config.basedir)
            locations = []
            for location in renpy.loadsave.location.locations:
                path = os.path.realpath(location.directory)
                if path.startswith(project + os.sep):
                    locations.append(os.path.relpath(path, project))
            report = os.path.join(project, '.vn-stream', 'save-locations.json')
            with open(report + '.tmp', 'w') as output:
                json.dump(dict(locations=locations), output)
            os.rename(report + '.tmp', report)
        except Exception:
            pass
        quit_pending = [False]
        quit_before = [set()]
        quit_seen = [set()]
        quit_deadline = [0]
        last_quit_request = [None]

        def shown_screens():
            names = set()
            for entries in renpy.game.context().scene_lists.layers.values():
                for entry in entries:
                    screen = entry.displayable
                    if isinstance(screen, renpy.display.screen.ScreenDisplayable):
                        names.add(tuple(screen.screen_name))
            return names

        def update_quit_pending():
            if not quit_pending[0]:
                return
            shown = shown_screens()
            quit_seen[0].update(shown - quit_before[0])
            prompt = any(renpy.display.screen.get_screen(name) for name in ('confirm', 'yesno_prompt'))
            if (time.time() >= quit_deadline[0] and not renpy.game.interface.in_quit_event
                    and not prompt and not (shown & quit_seen[0])):
                quit_pending[0] = False
        last_status = [None]

        def write(path, value):
            temporary = path + '.tmp'
            with open(temporary, 'w') as output:
                json.dump(value, output)
            os.rename(temporary, path)

        def status():
            update_quit_pending()
            available = ['menu', 'quit']
            if renpy.store.Skip().get_sensitive():
                available.append('skip')
            if renpy.exports.can_rollback():
                available.append('rollback')
            for name in ['save', 'load']:
                if renpy.display.screen.has_screen(name) and renpy.store.ShowMenu(name).get_sensitive():
                    available.append(name)
            value = dict(ready=True, skip=bool(config.skipping), available=available,
                         quit_pending=quit_pending[0], last_quit_request=last_quit_request[0])
            if value != last_status[0]:
                write(os.path.join(directory, 'status.json'), value)
                last_status[0] = value

        def poll():
            try:
                if config.skipping:
                    try:
                        with open(media_path) as source:
                            media = json.load(source)
                        live = media.get('session_id') == session and media.get('connected') is True and 0 <= time.time() - media.get('wall', 0) <= 3
                    except (IOError, ValueError):
                        live = False
                    if not live:
                        config.skipping = None
                status()
                commands = os.path.join(directory, 'commands')
                names = sorted(n for n in os.listdir(commands) if n.endswith('.json'))
                if not names:
                    return
                filename = os.path.join(commands, names[0])
                with open(filename) as source:
                    command = json.load(source)
                os.unlink(filename)  # Consume before nested interactions or control exceptions.
                action = command.get('action')
                with open(media_path) as source:
                    owner = json.load(source)
                allowed = (command.get('session_id') == session and owner.get('session_id') == session
                           and owner.get('connected') is True
                           and 0 <= time.time() - owner.get('wall', 0) <= 3
                           and command.get('connection_id') == owner.get('connection_id')
                           and command.get('epoch') == owner.get('epoch'))
                if not allowed or action not in ['rollback', 'skip', 'save', 'load', 'menu', 'quit']:
                    command.update(state='rejected', reason='not_controller')
                elif action == 'quit' and quit_pending[0]:
                    command.update(state='rejected', reason='quit_pending')
                elif action not in last_status[0]['available']:
                    command.update(state='rejected', reason='unavailable')
                else:
                    command['state'] = 'dispatched'
                write(os.path.join(directory, 'requests', command['request_id'] + '.json'), command)
            except (IOError, OSError, ValueError, KeyError, TypeError):
                return
            if command['state'] != 'dispatched':
                return
            # Do not swallow Ren'Py's Quit/Jump/Rollback control exceptions.
            if action == 'quit':
                last_quit_request[0] = command['request_id']
                quit_before[0] = shown_screens()
                quit_seen[0] = set()
                quit_deadline[0] = time.time() + 1.0
                quit_pending[0] = True
                status()
                renpy.exports.quit_event()
            elif action == 'menu':
                renpy.display.behavior.queue_event('game_menu')
            else:
                selected = (renpy.store.Rollback() if action == 'rollback' else
                            renpy.store.Skip() if action == 'skip' else renpy.store.ShowMenu(action))
                if selected.get_sensitive():
                    renpy.display.behavior.run(selected)
            status()

        config.periodic_callbacks.append(poll)
    renpy.game.post_init.append(_stream_actions_init)
