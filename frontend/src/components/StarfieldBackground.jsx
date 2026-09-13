const stars = Array.from({ length: 72 }, (_, index) => ({
  id: index,
  left: `${(index * 37) % 100}%`,
  top: `${(index * 61) % 100}%`,
  size: `${index % 5 === 0 ? 2 : 1}px`,
  delay: `${(index % 9) * -0.45}s`,
}));

export default function StarfieldBackground() {
  return <div className="pointer-events-none fixed inset-0 z-0 overflow-hidden" aria-hidden="true">
    {stars.map((star) => <span key={star.id} className="absolute rounded-full bg-holo-200 animate-twinkle" style={{ left: star.left, top: star.top, width: star.size, height: star.size, animationDelay: star.delay }} />)}
    <div className="scanlines" />
  </div>;
}
