import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from "react"
import { useNavigate } from "react-router-dom"
import { setToken, clearToken, setOnUnauthorized, probeSession } from "../api/auth"
import type { AuthUser, AuthResponse } from "../api/auth"
import * as authApi from "../api/auth"
import { useToast } from "../pages/editor/components/Toast"

interface AuthContextValue {
  user: AuthUser | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (username: string, email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue>(null!)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()
  const { addToast } = useToast()

  useEffect(() => {
    setOnUnauthorized(() => {
      setUser(null)
      addToast('登录已过期，请重新登录', 'warning')
      navigate('/login')
    })
    return () => setOnUnauthorized(null)
  }, [navigate, addToast])

  useEffect(() => {
    let cancelled = false
    probeSession()
      .then(u => { if (!cancelled && u) setUser(u) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const res: AuthResponse = await authApi.login(email, password)
    setToken(res.token)
    setUser(res.user)
  }, [])

  const register = useCallback(async (username: string, email: string, password: string) => {
    const res: AuthResponse = await authApi.register(username, email, password)
    setToken(res.token)
    setUser(res.user)
  }, [])

  const logout = useCallback(() => {
    authApi.logout()
    clearToken()
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used within AuthProvider")
  return ctx
}
