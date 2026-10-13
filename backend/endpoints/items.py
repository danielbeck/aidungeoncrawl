from flask import Blueprint, jsonify, request, current_app, session

bp = Blueprint('items', __name__, url_prefix='/items')

def get_db():
    # Import from app context
    return current_app.config['get_db']()

def dict_from_row(row):
    return current_app.config['dict_from_row'](row)

@bp.route('/', methods=['GET'])
def get_items():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    try:
        conn = get_db()
        items = conn.execute('SELECT * FROM items WHERE user_id = ? ORDER BY id', (user_id,)).fetchall()
        conn.close()
        return jsonify({'items': [dict_from_row(i) for i in items]})
    except Exception as e:
        current_app.logger.error(f'Error fetching items for user {user_id}: {str(e)}')
        return jsonify({'error': 'Failed to fetch items'}), 500

@bp.route('/', methods=['POST'])
def create_item():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        data = request.json
    except Exception:
        return jsonify({'error': 'Invalid JSON data'}), 400
    
    if not data or 'name' not in data:
        return jsonify({'error': 'Name is required'}), 400
    
    # Validate input lengths
    name = data.get('name', '').strip()
    description = data.get('description', '').strip()
    
    if not name:
        return jsonify({'error': 'Name cannot be empty'}), 400
    if len(name) > 255:
        return jsonify({'error': 'Name must be less than 255 characters'}), 400
    if len(description) > 1000:
        return jsonify({'error': 'Description must be less than 1000 characters'}), 400
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO items (user_id, name, description)
            VALUES (?, ?, ?)
        ''', (user_id, name, description))
        item_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return jsonify({'id': item_id, 'message': 'Item created'}), 201
    except Exception as e:
        current_app.logger.error(f'Error creating item for user {user_id}: {str(e)}')
        if 'conn' in locals():
            conn.close()
        return jsonify({'error': 'Failed to create item'}), 500

@bp.route('/<int:item_id>', methods=['GET'])
def get_item(item_id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    try:
        conn = get_db()
        item = conn.execute('SELECT * FROM items WHERE id = ? AND user_id = ?', (item_id, user_id)).fetchone()
        conn.close()
        if item:
            return jsonify({'item': dict_from_row(item)})
        return jsonify({'error': 'Item not found'}), 404
    except Exception as e:
        current_app.logger.error(f'Error fetching item {item_id} for user {user_id}: {str(e)}')
        return jsonify({'error': 'Failed to fetch item'}), 500

@bp.route('/<int:item_id>', methods=['PUT'])
def update_item(item_id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        data = request.json
    except Exception:
        return jsonify({'error': 'Invalid JSON data'}), 400
    
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        fields = []
        values = []
        
        if 'name' in data:
            name = data['name'].strip()
            if not name:
                return jsonify({'error': 'Name cannot be empty'}), 400
            if len(name) > 255:
                return jsonify({'error': 'Name must be less than 255 characters'}), 400
            fields.append('name = ?')
            values.append(name)
        
        if 'description' in data:
            description = data['description'].strip()
            if len(description) > 1000:
                return jsonify({'error': 'Description must be less than 1000 characters'}), 400
            fields.append('description = ?')
            values.append(description)
        
        if not fields:
            return jsonify({'error': 'No fields to update'}), 400
        
        fields.append('updated_at = CURRENT_TIMESTAMP')
        values.extend([item_id, user_id])
        
        # Build query with validated fields
        query = f"UPDATE items SET {', '.join(fields)} WHERE id = ? AND user_id = ?"
        cursor.execute(query, values)
        
        if cursor.rowcount == 0:
            conn.close()
            return jsonify({'error': 'Item not found or access denied'}), 404
        
        conn.commit()
        conn.close()
        return jsonify({'message': 'Item updated'})
    except Exception as e:
        current_app.logger.error(f'Error updating item {item_id} for user {user_id}: {str(e)}')
        if conn:
            conn.close()
        return jsonify({'error': 'Failed to update item'}), 500

@bp.route('/<int:item_id>', methods=['DELETE'])
def delete_item(item_id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM items WHERE id = ? AND user_id = ?', (item_id, user_id))
        
        if cursor.rowcount == 0:
            conn.close()
            return jsonify({'error': 'Item not found or access denied'}), 404
        
        conn.commit()
        conn.close()
        return jsonify({'message': 'Item deleted'})
    except Exception as e:
        current_app.logger.error(f'Error deleting item {item_id} for user {user_id}: {str(e)}')
        if 'conn' in locals():
            conn.close()
        return jsonify({'error': 'Failed to delete item'}), 500
