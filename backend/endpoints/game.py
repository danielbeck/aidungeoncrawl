"""Authenticated game-state and gameplay turn API."""
import json
import queue
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from flask import Blueprint, Response, current_app, jsonify, request, session

try:
    from backend.direct_mode import process_direct_turn
    from backend.freeform_mode import process_freeform_turn
    from backend.tool_mode import process_tool_turn
    from backend.game_engine import STARTING_POINTS, InvalidTurnResponse, new_game, process_turn
    from backend.harness.llm_provider import LLMError
    from backend.llm_calls.gameplay import HistorySummarizerCall
except ImportError:
    from direct_mode import process_direct_turn
    from freeform_mode import process_freeform_turn
    from tool_mode import process_tool_turn
    from game_engine import STARTING_POINTS, InvalidTurnResponse, new_game, process_turn
    from harness.llm_provider import LLMError
    from llm_calls.gameplay import HistorySummarizerCall


bp = Blueprint('game', __name__, url_prefix='/game')
MODES = ('managed', 'direct', 'freeform', 'tool_using')
DEFAULT_DAILY_TURN_QUOTA = 200


def _stream_event(event):
    return json.dumps(event, ensure_ascii=False) + '\n'


def _stream_turn(work):
    app = current_app._get_current_object()
    events = queue.Queue()

    def run():
        with app.app_context():
            try:
                response = work(lambda event: events.put({'type': 'progress', **event}))
                if isinstance(response, tuple):
                    response, status = response
                else:
                    status = response.status_code
                data = response.get_json()
                events.put({'type': 'complete' if status < 400 else 'error',
                            'status': status, 'data': data})
            except Exception:
                app.logger.exception('Unexpected streamed gameplay failure')
                events.put({'type': 'error', 'status': 500,
                            'data': {'error': 'The turn could not complete. Reload before retrying.'}})

    def generate():
        threading.Thread(target=run, daemon=True).start()
        yield _stream_event({'type': 'progress', 'component': 'turn', 'status': 'started'})
        while True:
            try:
                event = events.get(timeout=10)
            except queue.Empty:
                yield _stream_event({'type': 'heartbeat'})
                continue
            yield _stream_event(event)
            if event['type'] in ('complete', 'error'):
                break

    return Response(generate(), mimetype='application/x-ndjson',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


def _get_db():
    return current_app.config['get_db']()


def _ensure_schema(conn):
    conn.execute('''
        CREATE TABLE IF NOT EXISTS games (
            user_id INTEGER PRIMARY KEY,
            state_json TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS game_turn_requests (
            user_id INTEGER NOT NULL,
            game_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_id, game_id, request_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS user_turn_usage (
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            turns INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')


def _daily_turn_quota():
    """The per-account daily turn allowance, from settings or the default."""
    raw = (current_app.config.get('llm_settings') or {}).get('USER_DAILY_TURN_QUOTA')
    try:
        quota = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_DAILY_TURN_QUOTA
    return quota if quota > 0 else DEFAULT_DAILY_TURN_QUOTA


def _consume_turn(conn, user_id):
    """Count one turn against today's durable per-account allowance.

    The counter lives in SQLite so it survives CGI processes and restarts.
    Attempts are counted when they start, because a turn that later fails has
    still used provider capacity. Returns the remaining allowance, or None when
    the allowance for today is already spent.
    """
    day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    limit = _daily_turn_quota()
    conn.execute('BEGIN IMMEDIATE')
    row = conn.execute(
        'SELECT turns FROM user_turn_usage WHERE user_id = ? AND day = ?', (user_id, day)
    ).fetchone()
    used = row['turns'] if row else 0
    if used >= limit:
        conn.rollback()
        return None
    conn.execute('''
        INSERT INTO user_turn_usage (user_id, day, turns) VALUES (?, ?, 1)
        ON CONFLICT(user_id, day) DO UPDATE SET turns = user_turn_usage.turns + 1
    ''', (user_id, day))
    conn.commit()
    return limit - used - 1


def _user_id():
    user_id = session.get('user_id')
    if not user_id:
        return None
    return user_id


def _load_row(conn, user_id):
    return conn.execute(
        'SELECT state_json, version FROM games WHERE user_id = ?', (user_id,)
    ).fetchone()


@bp.route('/', methods=['GET'])
def get_game():
    user_id = _user_id()
    if not user_id:
        return jsonify({'error': 'Authentication is required to play.'}), 401
    conn = _get_db()
    try:
        conn.execute('BEGIN IMMEDIATE')
        _ensure_schema(conn)
        row = _load_row(conn, user_id)
        if row is None:
            game = new_game(user_id)
            conn.execute(
                'INSERT INTO games (user_id, state_json, version) VALUES (?, ?, 0)',
                (user_id, json.dumps(game)),
            )
            version = 0
        else:
            game, version = json.loads(row['state_json']), row['version']
        conn.commit()
        return jsonify({'game': game, 'version': version})
    except Exception:
        conn.rollback()
        current_app.logger.exception('Failed to load game for user %s', user_id)
        return jsonify({'error': 'Unable to load the game right now.'}), 500
    finally:
        conn.close()


@bp.route('/new', methods=['POST'])
def start_new_game():
    user_id = _user_id()
    if not user_id:
        return jsonify({'error': 'Authentication is required to play.'}), 401
    data = request.get_json(silent=True) if request.get_data() else {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Expected a JSON object.'}), 400
    starting_point = data.get('starting_point', 'town')
    if not isinstance(starting_point, str) or starting_point not in STARTING_POINTS:
        return jsonify({'error': f'starting_point must be one of: {", ".join(STARTING_POINTS)}.'}), 400
    game = new_game(user_id, starting_point=starting_point)
    conn = _get_db()
    try:
        _ensure_schema(conn)
        conn.execute('''
            INSERT INTO games (user_id, state_json, version)
            VALUES (?, ?, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                state_json = excluded.state_json,
                version = games.version + 1,
                updated_at = CURRENT_TIMESTAMP
        ''', (user_id, json.dumps(game)))
        conn.commit()
        row = _load_row(conn, user_id)
        return jsonify({'game': game, 'version': row['version']})
    except Exception:
        conn.rollback()
        current_app.logger.exception('Failed to start game for user %s', user_id)
        return jsonify({'error': 'Unable to start a new game right now.'}), 500
    finally:
        conn.close()


@bp.route('/mode', methods=['POST'])
def change_mode():
    user_id = _user_id()
    if not user_id:
        return jsonify({'error': 'Authentication is required to play.'}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get('mode') not in MODES:
        return jsonify({'error': f'mode must be one of: {", ".join(MODES)}.'}), 400
    if not isinstance(data.get('game_id'), str):
        return jsonify({'error': 'game_id is required.'}), 400
    conn = _get_db()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = _load_row(conn, user_id)
        if row is None:
            conn.rollback()
            return jsonify({'error': 'Start a game before changing mode.'}), 409
        game = json.loads(row['state_json'])
        if game['id'] != data['game_id']:
            conn.rollback()
            return jsonify({'error': 'The game has changed. Reload before changing mode.'}), 409
        game['mode'] = data['mode']
        conn.execute('''
            UPDATE games SET state_json = ?, version = version + 1,
                updated_at = CURRENT_TIMESTAMP WHERE user_id = ?
        ''', (json.dumps(game, ensure_ascii=False), user_id))
        conn.commit()
        return jsonify({'game': game})
    except sqlite3.Error:
        conn.rollback()
        current_app.logger.exception('Failed to change game mode for user %s', user_id)
        return jsonify({'error': 'Unable to save the storytelling mode.'}), 500
    finally:
        conn.close()


@bp.route('/prepare', methods=['POST'])
def prepare_history():
    user_id = _user_id()
    if not user_id:
        return jsonify({'error': 'Authentication is required to play.'}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('game_id'), str):
        return jsonify({'error': 'game_id is required.'}), 400
    conn = _get_db()
    try:
        row = _load_row(conn, user_id)
        if row is None:
            return jsonify({'error': 'Start a game before preparing history.'}), 409
        state = json.loads(row['state_json'])
        version = row['version']
    finally:
        conn.close()
    if state['id'] != data['game_id']:
        return jsonify({'prepared': False, 'reason': 'The game has changed.'})
    history = state['history']
    count = len(history['recentEvents'])
    if history.get('summaryEventCount') == count and history.get('summary'):
        return jsonify({'prepared': True, 'summary': history['summary'],
                        'summaryEventCount': count})
    try:
        result = HistorySummarizerCall(settings=current_app.config['llm_settings']).run(
            json.dumps({'completed_history': history['recentEvents']}, ensure_ascii=False))
        summary = HistorySummarizerCall.validate_response(result.data)['summary']
    except LLMError as error:
        current_app.logger.exception('History preparation failed for user %s', user_id)
        return jsonify({'error': f'History preparation failed: {error} '
                        'Your turn is saved; preparation can be retried.'}), 502
    conn = _get_db()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = _load_row(conn, user_id)
        if row is None or row['version'] != version:
            conn.rollback()
            return jsonify({'prepared': False, 'reason': 'A newer game revision is already saved.'})
        current = json.loads(row['state_json'])
        if current['id'] != state['id']:
            conn.rollback()
            return jsonify({'prepared': False, 'reason': 'The game has changed.'})
        current['history']['summary'] = summary
        current['history']['summaryEventCount'] = count
        conn.execute('UPDATE games SET state_json = ? WHERE user_id = ?',
                     (json.dumps(current, ensure_ascii=False), user_id))
        conn.commit()
        return jsonify({'prepared': True, 'summary': summary, 'summaryEventCount': count,
                        'provider': result.provider, 'model': result.model})
    except sqlite3.Error:
        conn.rollback()
        current_app.logger.exception('Failed to save prepared history for user %s', user_id)
        return jsonify({'error': 'Prepared history could not be saved.'}), 500
    finally:
        conn.close()


@bp.route('/turn', methods=['POST'])
def play_turn():
    user_id = _user_id()
    if not user_id:
        return jsonify({'error': 'Authentication is required to play.'}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'A JSON request body is required.'}), 400
    action = data.get('action')
    request_id = data.get('request_id')
    game_id = data.get('game_id')
    mode = data.get('mode', 'managed')
    if mode not in MODES:
        return jsonify({'error': f'mode must be one of: {", ".join(MODES)}.'}), 400
    if not isinstance(action, str) or not action.strip() or len(action) > 2000:
        return jsonify({'error': 'action must contain 1 to 2000 characters.'}), 400
    if not isinstance(request_id, str):
        return jsonify({'error': 'request_id is required.'}), 400
    try:
        request_id = str(uuid.UUID(request_id))
    except (ValueError, AttributeError):
        return jsonify({'error': 'request_id must be a valid UUID.'}), 400
    if not isinstance(game_id, str) or not game_id:
        return jsonify({'error': 'game_id is required.'}), 400

    conn = _get_db()
    try:
        _ensure_schema(conn)
        row = _load_row(conn, user_id)
        if row is None:
            return jsonify({'error': 'Start a game before taking an action.'}), 409
        original_state = json.loads(row['state_json'])
        original_version = row['version']
        if original_state['id'] != game_id:
            return jsonify({'error': 'The game has changed. Reload before taking another action.'}), 409
        previous = conn.execute('''
            SELECT response_json FROM game_turn_requests
            WHERE user_id = ? AND game_id = ? AND request_id = ?
        ''', (user_id, game_id, request_id)).fetchone()
        if previous:
            if request.accept_mimetypes.best == 'application/x-ndjson':
                return Response(_stream_event({
                    'type': 'complete', 'status': 200,
                    'data': json.loads(previous['response_json']),
                }), mimetype='application/x-ndjson')
            return jsonify(json.loads(previous['response_json']))
        if original_state['status'] != 'active':
            return jsonify({'error': 'This game has ended. Start a new game to continue.'}), 409
        remaining = _consume_turn(conn, user_id)
        if remaining is None:
            return jsonify({
                'error': f'You have reached the daily limit of {_daily_turn_quota()} game turns. '
                         'Your adventure is saved; you can continue tomorrow.',
            }), 429
    finally:
        conn.close()

    if request.accept_mimetypes.best == 'application/x-ndjson':
        return _stream_turn(lambda on_progress: _complete_turn(
            user_id, game_id, request_id, action, original_state,
            original_version, on_progress, mode))
    return _complete_turn(user_id, game_id, request_id, action,
                          original_state, original_version, mode=mode)


def _complete_turn(user_id, game_id, request_id, action, original_state,
                   original_version, on_progress=None, mode='managed'):
    current_app.logger.info('Starting gameplay turn for user %s, game %s', user_id, game_id)
    try:
        options = {'settings': current_app.config['llm_settings']}
        if on_progress is not None:
            options['on_progress'] = on_progress
        processor = {
            'managed': process_turn,
            'direct': process_direct_turn,
            'freeform': process_freeform_turn,
            'tool_using': process_tool_turn,
        }[mode]
        updated_state, result = processor(original_state, action, **options)
        updated_state['mode'] = mode
    except LLMError as error:
        current_app.logger.error('LLM gameplay turn failed for user %s: %s', user_id, error)
        return jsonify({
            'error': f'The game could not complete that turn: {error} Your state was not changed.',
        }), 502
    except InvalidTurnResponse as error:
        current_app.logger.warning('Rejected gameplay response for user %s: %s', user_id, error)
        return jsonify({
            'error': f'The game returned invalid turn data: {error} Your state was not changed.',
        }), 502
    except (KeyError, TypeError, ValueError) as error:
        current_app.logger.exception('Invalid gameplay response for user %s: %s', user_id, error)
        return jsonify({
            'error': f'The game returned an invalid response: {type(error).__name__}: '
                     f'{error} Your state was not changed.',
        }), 502

    response = {
        'game': updated_state,
        'turn': {
            'action': action.strip(),
            'mode': mode,
            **result,
        },
    }
    if on_progress is not None:
        on_progress({'component': 'turn', 'status': 'saving'})
    conn = _get_db()
    try:
        conn.execute('BEGIN IMMEDIATE')
        current = _load_row(conn, user_id)
        if current is None:
            conn.rollback()
            return jsonify({'error': 'The game changed while processing. Reload and try again.'}), 409
        current_state = json.loads(current['state_json'])
        if current_state['id'] != game_id or current['version'] != original_version:
            duplicate = conn.execute('''
                SELECT response_json FROM game_turn_requests
                WHERE user_id = ? AND game_id = ? AND request_id = ?
            ''', (user_id, game_id, request_id)).fetchone()
            conn.rollback()
            if duplicate:
                return jsonify(json.loads(duplicate['response_json']))
            return jsonify({'error': 'The game changed while processing. Reload and try again.'}), 409
        conn.execute('''
            UPDATE games SET state_json = ?, version = version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        ''', (json.dumps(updated_state, ensure_ascii=False), user_id))
        conn.execute('''
            INSERT INTO game_turn_requests (user_id, game_id, request_id, response_json)
            VALUES (?, ?, ?, ?)
        ''', (user_id, game_id, request_id, json.dumps(response, ensure_ascii=False)))
        conn.commit()
        current_app.logger.info('Committed gameplay turn for user %s, game %s', user_id, game_id)
        return jsonify(response)
    except sqlite3.IntegrityError:
        conn.rollback()
        duplicate = conn.execute('''
            SELECT response_json FROM game_turn_requests
            WHERE user_id = ? AND game_id = ? AND request_id = ?
        ''', (user_id, game_id, request_id)).fetchone()
        if duplicate:
            return jsonify(json.loads(duplicate['response_json']))
        current_app.logger.exception('Failed to commit gameplay turn for user %s', user_id)
        return jsonify({'error': 'The turn could not be saved. Reload and try again.'}), 409
    except Exception:
        conn.rollback()
        current_app.logger.exception('Failed to commit gameplay turn for user %s', user_id)
        return jsonify({'error': 'The turn could not be saved. Reload and try again.'}), 500
    finally:
        conn.close()


@bp.route('/stop', methods=['POST'])
def stop_game():
    user_id = _user_id()
    if not user_id:
        return jsonify({'error': 'Authentication is required to play.'}), 401
    conn = _get_db()
    try:
        _ensure_schema(conn)
        conn.execute('BEGIN IMMEDIATE')
        row = _load_row(conn, user_id)
        if row is None:
            conn.rollback()
            return jsonify({'error': 'There is no active game.'}), 404
        game = json.loads(row['state_json'])
        if game['status'] == 'active':
            game['status'] = 'ended'
            game['endReason'] = 'player_stopped'
            conn.execute('''
                UPDATE games SET state_json = ?, version = version + 1,
                    updated_at = CURRENT_TIMESTAMP WHERE user_id = ?
            ''', (json.dumps(game), user_id))
        conn.commit()
        return jsonify({'game': game})
    except Exception:
        conn.rollback()
        current_app.logger.exception('Failed to stop game for user %s', user_id)
        return jsonify({'error': 'Unable to stop the game right now.'}), 500
    finally:
        conn.close()
