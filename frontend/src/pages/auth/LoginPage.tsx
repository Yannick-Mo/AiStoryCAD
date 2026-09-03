import { useState, type FormEvent } from "react"
import { useAuth } from "../../context/AuthContext"

export default function LoginPage() {
  const { login, register } = useAuth()
  const [isRegister, setIsRegister] = useState(false)
  const [username, setUsername] = useState("")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError("")
    setBusy(true)
    try {
      if (isRegister) {
        await register(username, email, password)
      } else {
        await login(email, password)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-amber-500">AiStoryCAD</h1>
          <p className="text-gray-500 text-sm mt-1">叙事架构设计工具</p>
        </div>

        <form onSubmit={handleSubmit} className="bg-gray-900 border border-gray-800 rounded-2xl p-6 space-y-4">
          <h2 className="text-lg font-medium text-gray-200 text-center">
            {isRegister ? "注册新账号" : "登录"}
          </h2>

          {error && (
            <div className="bg-red-900/30 border border-red-800 text-red-300 text-sm rounded-lg px-3 py-2">
              {error}
            </div>
          )}

          {isRegister && (
            <div>
              <label htmlFor="login-username" className="block text-xs text-gray-400 mb-1">用户名</label>
              <input
                id="login-username"
                value={username}
                onChange={e => setUsername(e.target.value)}
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-amber-600"
                placeholder="your name"
                required
              />
            </div>
          )}

          <div>
            <label htmlFor="login-email" className="block text-xs text-gray-400 mb-1">邮箱</label>
            <input
              id="login-email"
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-amber-600"
              placeholder="you@example.com"
              required
            />
          </div>

          <div>
            <label htmlFor="login-password" className="block text-xs text-gray-400 mb-1">密码</label>
            <input
              id="login-password"
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-amber-600"
              placeholder="••••••••"
              required
            />
          </div>

          <button
            type="submit"
            disabled={busy}
            className="w-full py-2 rounded-lg bg-amber-600 text-sm font-medium text-black hover:bg-amber-500 transition-colors disabled:opacity-50"
          >
            {busy ? "请稍候..." : isRegister ? "注册" : "登录"}
          </button>

          <div className="text-center">
            <button
              type="button"
              onClick={() => { setIsRegister(!isRegister); setError("") }}
              className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
            >
              {isRegister ? "已有账号？去登录" : "没有账号？去注册"}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
