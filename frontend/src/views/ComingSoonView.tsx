interface ComingSoonViewProps {
  name: string
}

export default function ComingSoonView({ name }: ComingSoonViewProps) {
  return (
    <div className="coming-soon" role="main" aria-label={name}>
      <p className="coming-soon-title">{name}</p>
      <p className="coming-soon-caption">Próximamente</p>
    </div>
  )
}
