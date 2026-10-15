import { useEffect, useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { fetchAPI } from './api'
import AuthPage from './components/AuthPage'
import GamePage from './components/GamePage'
import PasswordRecoveryPage from './components/PasswordRecoveryPage'
import ProfilePage from './components/ProfilePage'
import './global.scss'

function App() {
  const [user, setUser] = useState(null)
  const [userLoading, setUserLoading] = useState(true)
  const navigate = useNavigate()
  const location = useLocation()

  useEffect(() => {
    fetchAPI('/auth/me', { credentials: 'include' })
      .then(data => setUser(data.user))
      .catch(() => setUser(null))
      .finally(() => setUserLoading(false))
  }, [])

  const handleAuth = (data) => setUser(data.email || data.user)

  const handleLogout = async () => {
    await fetchAPI('/auth/logout', { method: 'POST', credentials: 'include' })
    setUser(null)
    navigate('/')
  }

  return (
    <div className="App">
      <header className="App-header">

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
        {userLoading && location.pathname === '/' && <p role="status">Loading...</p>}
      </header>

      <main>
        <Routes>
          <Route path="/auth" element={<AuthPage onAuth={handleAuth} />} />
          <Route path="/forgot-password" element={<PasswordRecoveryPage key="forgot" />} />
          <Route path="/reset-password" element={<PasswordRecoveryPage key="reset" reset onReset={() => setUser(null)} />} />
          <Route path="/profile" element={userLoading
            ? <section className="auth-page"><p role="status">Loading your profile...</p></section>
            : user ? <ProfilePage user={user} onLogout={handleLogout} />
              : <Navigate to="/auth" replace />} />
          <Route path="/" element={userLoading
            ? null
            : user
              ? <GamePage />
              : <section className="welcome-message" aria-labelledby="welcome-title">
                <p className="welcome-eyebrow">A tiny text adventure</p>
                <h2 id="welcome-title">Go on... an adventure!</h2>
                <Link className="welcome-cta" to="/auth">Let&apos;s play</Link>
                <p className="welcome-note">Your choices shape the story. The world grows around you.
                  You gotta create a free account so we have somewhere to store your progress. No spam, promise.</p>
              </section>
          } />
        </Routes>
      </main>
    </div>
  )
}

export default App
