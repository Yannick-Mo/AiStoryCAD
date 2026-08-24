import { Routes, Route, Navigate } from 'react-router-dom'
import ProjectListPage from './pages/home'
import ProjectPage from './pages/editor'
import { ToastProvider } from './pages/editor/components/Toast'

export default function App() {
  return (
    <ToastProvider>
      <Routes>
        <Route path="/" element={<ProjectListPage />} />
        <Route path="/projects/:id" element={<ProjectPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </ToastProvider>
  )
}
