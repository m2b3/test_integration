import { useMemo, useState } from 'react'

const SUGGESTED_INTERESTS = [
  'biology',
  'bioinformatics',
  'cancer biology',
  'chemistry',
  'climate science',
  'computer vision',
  'C (programming language)',
  'C++ (programming language)',
  'CRISPR',
  'epidemiology',
  'genomics',
  'immunology',
  'machine learning',
  'medicine',
  'natural language processing',
  'neuroscience',
  'physics',
  'public health',
  'statistics',
]

function normalized(value) {
  return value.trim().replace(/\s+/g, ' ')
}

function suggestionRank(suggestion, input) {
  const item = suggestion.toLowerCase()
  const query = input.toLowerCase()

  if (item === query) {
    return 0
  }
  if (item.startsWith(query)) {
    return 1
  }
  if (item.split(/\s+/).some((word) => word.startsWith(query))) {
    return 2
  }
  if (item.includes(query)) {
    return 3
  }
  return null
}

function InterestInput({ interests, onChange }) {
  const [inputValue, setInputValue] = useState('')

  const suggestions = useMemo(() => {
    const value = normalized(inputValue)
    if (!value) {
      return []
    }

    return SUGGESTED_INTERESTS
      .filter((suggestion) => !interests.some((interest) => interest.toLowerCase() === suggestion.toLowerCase()))
      .map((suggestion) => ({
        suggestion,
        rank: suggestionRank(suggestion, value),
      }))
      .filter((item) => item.rank !== null)
      .sort((a, b) => a.rank - b.rank || a.suggestion.localeCompare(b.suggestion))
      .slice(0, 6)
      .map((item) => item.suggestion)
  }, [inputValue, interests])

  function addInterest(value) {
    const nextInterest = normalized(value)
    if (!nextInterest) {
      return
    }

    const alreadyExists = interests.some(
      (interest) => interest.toLowerCase() === nextInterest.toLowerCase(),
    )
    if (!alreadyExists) {
      onChange([...interests, nextInterest])
    }
    setInputValue('')
  }

  function removeInterest(value) {
    onChange(interests.filter((interest) => interest !== value))
  }

  function handleKeyDown(event) {
    if (event.key === 'Enter') {
      event.preventDefault()
      addInterest(inputValue)
    }
  }

  return (
    <div className="interest-input">
      <label className="field">
        <span>Fields of interest</span>
        <input
          onChange={(event) => setInputValue(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type an interest and press Enter"
          type="text"
          value={inputValue}
        />
      </label>

      {suggestions.length > 0 && (
        <div className="interest-suggestions">
          {suggestions.map((suggestion) => (
            <button key={suggestion} type="button" onClick={() => addInterest(suggestion)}>
              {suggestion}
            </button>
          ))}
        </div>
      )}

      <div className="interest-tags" aria-label="Selected interests">
        {interests.map((interest) => (
          <span className="interest-chip" key={interest}>
            {interest}
            <button type="button" aria-label={`Remove ${interest}`} onClick={() => removeInterest(interest)}>
              x
            </button>
          </span>
        ))}
      </div>
    </div>
  )
}

export default InterestInput
