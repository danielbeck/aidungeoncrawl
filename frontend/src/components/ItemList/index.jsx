import styles from './ItemList.module.scss';

function ItemList({ items, onDelete }) {
    return (
        <div className={styles.itemList}>
            <h2>Items ({items.length})</h2>
            <ul>
                {items.map(item => (
                    <li key={item.id} className={styles.item}>
                        <span className={styles.name}>{item.name}</span>
                        {item.description && <span className={styles.description}> - {item.description}</span>}
                        <button className={styles.deleteButton} onClick={() => onDelete(item.id)}>Delete</button>
                    </li>
                ))}
            </ul>
        </div>
    );
}

export default ItemList;
