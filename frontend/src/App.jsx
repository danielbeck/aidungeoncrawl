
import { useState, useEffect } from 'react'
import { Link, Navigate, Routes, Route, useLocation, useNavigate } from 'react-router-dom'
import { fetchAPI } from './api'
import ItemList from './components/ItemList'
import ItemForm from './components/ItemForm'
import AuthPage from './components/AuthPage'
import ProfilePage from './components/ProfilePage'
import PasswordRecoveryPage from './components/PasswordRecoveryPage'
import LLMDebugWidget from './components/LLMDebugWidget'
import RoomGenerator from './components/RoomGenerator'
import './global.scss'


function App() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [newItemName, setNewItemName] = useState('')
  const [newItemDesc, setNewItemDesc] = useState('')
  const [user, setUser] = useState(null)
  const [userLoading, setUserLoading] = useState(true)
  const navigate = useNavigate();
  const location = useLocation()

  useEffect(() => {
    fetchCurrentUser()
  }, [])

  const fetchCurrentUser = async () => {
    try {
      const data = await fetchAPI('/auth/me', { credentials: 'include' })
      setUser(data.user)
      if (data.user) fetchItems()
    } catch (err) {
      setUser(null)
    } finally {
      setUserLoading(false)
    }
  }

  const fetchItems = async () => {
    try {
      setLoading(true)
      const data = await fetchAPI('/items/', { credentials: 'include' })
      setItems(data.items || [])
      setError(null)
    } catch (err) {
      setError('Failed to fetch items: ' + err.message)
      console.error('Error fetching items:', err)
    } finally {
      setLoading(false)
    }
  }

  const createItem = async (e) => {
    e.preventDefault()
    if (!newItemName.trim()) return

    try {
      await fetchAPI('/items/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          name: newItemName,
          description: newItemDesc
        })
      })
      setNewItemName('')
      setNewItemDesc('')
      fetchItems()
    } catch (err) {
      setError('Failed to create item: ' + err.message)
    }
  }

  const deleteItem = async (id) => {
    try {
      await fetchAPI(`/items/${id}`, { method: 'DELETE', credentials: 'include' })
      fetchItems()
    } catch (err) {
      setError('Failed to delete item: ' + err.message)
    }
  }

  const handleAuth = (data) => {
    setUser(data.email || data.user)
    fetchItems()
  }

  const handleLogout = async () => {
    await fetchAPI('/auth/logout', { method: 'POST', credentials: 'include' })
    setUser(null)
    setItems([])
    navigate('/')
  }

  return (
    <div className="App">
      <header className="App-header">
        <h1>React + Python Boilerplate</h1>
        {(user || location.pathname.replace(/\/+$/, '') !== '/auth') && (
          <Link className="auth-link" to={user ? '/profile' : '/auth'}
            aria-label={user ? 'Your profile' : 'Log in or register'}
            title={user ? 'Your profile' : 'Log in or register'}>
            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
              <circle cx="12" cy="8" r="4" />
              <path d="M4 21v-1a8 8 0 0 1 16 0v1" />
            </svg>
          </Link>
        )}
        {location.pathname === '/' && user && loading && <p role="status">Loading...</p>}
        {location.pathname === '/' && error && <p style={{ color: 'red' }} role="alert">{error}</p>}
      </header>

      <main>
        <Routes>
          <Route path="/auth" element={<AuthPage onAuth={handleAuth} />} />
          <Route path="/forgot-password" element={<PasswordRecoveryPage key="forgot" />} />
          <Route path="/reset-password" element={<PasswordRecoveryPage key="reset" reset onReset={() => {
            setUser(null)
            setItems([])
          }} />} />
          <Route path="/profile" element={userLoading
            ? <section className="auth-page"><p role="status">Loading your profile...</p></section>
            : user ? <ProfilePage user={user} onLogout={handleLogout} />
              : <Navigate to="/auth" replace />} />
          <Route path="/" element={user ? (
            <>
              <ItemForm
                onSubmit={createItem}
                newItemName={newItemName}
                setNewItemName={setNewItemName}
                newItemDesc={newItemDesc}
                setNewItemDesc={setNewItemDesc}
              />
              <ItemList
                items={items}
                onDelete={deleteItem}
              />
            </>
          ) : (
            <div style={{ margin: '2em 0' }}>
              <p>Please <Link to="/auth">log in</Link> to view your todo list.</p>
            </div>
          )} />
        </Routes>
        <RoomGenerator />
        <LLMDebugWidget />
      </main>
    </div>
  )
}

export default App
