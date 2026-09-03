export default function HeroSection() {
  const hour = new Date().getHours()
  const greeting = hour < 12 ? "上午好" : hour < 18 ? "下午好" : "晚上好"

  return (
    <section className="pt-8 pb-4">
      <h1 className="text-2xl font-bold text-gray-100 flex items-center gap-2">
        {greeting}，AiStoryCAD 用户
        <span className="inline-block animate-wave text-2xl origin-[70%_70%]">👋</span>
      </h1>
      <p className="text-sm text-gray-400 mt-0.5">你的故事结构设计中心——让每一个灵感，都变成坚实的长篇蓝图。</p>
      <style>{`
        @keyframes wave { 0%,100%{transform:rotate(0deg)} 20%{transform:rotate(18deg)} 40%{transform:rotate(-10deg)} 60%{transform:rotate(14deg)} 80%{transform:rotate(-6deg)} }
        .animate-wave { animation: wave 1.5s ease-in-out infinite; }
      `}</style>
    </section>
  )
}
