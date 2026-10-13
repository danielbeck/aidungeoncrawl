import styles from './ItemForm.module.scss';

function ItemForm({ onSubmit, newItemName, setNewItemName, newItemDesc, setNewItemDesc }) {
    return (
        <div className={styles.createForm}>
            <h2>Add New Item</h2>
            <form onSubmit={onSubmit}>
                <input
                    className={styles.input}
                    type="text"
                    placeholder="Item name"
                    value={newItemName}
                    onChange={(e) => setNewItemName(e.target.value)}
                    required
                />
                <input
                    className={styles.input}
                    type="text"
                    placeholder="Description (optional)"
                    value={newItemDesc}
                    onChange={(e) => setNewItemDesc(e.target.value)}
                />
                <button className={styles.button} type="submit">Add Item</button>
            </form>
        </div>
    );
}

export default ItemForm;
