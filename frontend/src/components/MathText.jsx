import { useEffect, useRef } from 'react'

function MathText({ as = 'span', children, className = '' }) {
  const ref = useRef(null)

  useEffect(() => {
    if (window.MathJax?.typesetPromise && ref.current) {
      window.MathJax.typesetPromise([ref.current]).catch((error) => {
        console.error(error)
      })
    }
  }, [children])

  if (as === 'div') {
    return (
      <div className={className} ref={ref}>
        {children}
      </div>
    )
  }

  return (
    <span className={className} ref={ref}>
      {children}
    </span>
  )
}

export default MathText
