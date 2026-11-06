import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { CaretDownIcon, CheckIcon } from '@phosphor-icons/react';

/**
 * The site's own dropdown, used instead of the browser's native <select>
 * (whose open list is drawn by the OS and can't be styled).
 *
 * It is a button that opens a listbox. Keyboard: Arrow keys / Home / End move,
 * Enter or Space picks, Escape and Tab close, and typing a letter jumps to the
 * next option starting with it. The list flips above the button when there is
 * no room below. `required` is honoured through a hidden proxy input, so the
 * browser's normal "please fill out this field" check still runs on submit.
 *
 * options: [{ value, label }]. onChange receives the picked option's value.
 */
export default function Select({
  id,
  value,
  onChange,
  options,
  placeholder = 'Select…',
  required = false,
  disabled = false,
  buttonRef,
}) {
  const autoId = useId();
  const buttonId = id || autoId;
  const listId = `${buttonId}-list`;
  const wrapRef = useRef(null);
  const innerButtonRef = useRef(null);
  const listRef = useRef(null);
  const typed = useRef({ text: '', at: 0 });
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [up, setUp] = useState(false);

  const selectedIndex = options.findIndex((o) => o.value === value);
  const selected = selectedIndex >= 0 ? options[selectedIndex] : null;

  const setButton = (node) => {
    innerButtonRef.current = node;
    if (typeof buttonRef === 'function') buttonRef(node);
    else if (buttonRef) buttonRef.current = node;
  };

  const openList = () => {
    if (disabled || !options.length) return;
    setActive(selectedIndex >= 0 ? selectedIndex : 0);
    setOpen(true);
  };
  const closeList = () => setOpen(false);

  const pick = (index) => {
    const option = options[index];
    if (!option) return;
    if (option.value !== value) onChange(option.value);
    closeList();
    innerButtonRef.current?.focus({ preventScroll: true });
  };

  // Flip above the button when the list would run off the bottom of the screen.
  useLayoutEffect(() => {
    if (!open || !listRef.current || !innerButtonRef.current) return;
    const rect = innerButtonRef.current.getBoundingClientRect();
    const need = Math.min(listRef.current.scrollHeight, 280) + 12;
    const below = window.innerHeight - rect.bottom;
    setUp(below < need && rect.top > below);
  }, [open, options.length]);

  // Keep the highlighted option in view while arrowing through a long list.
  useEffect(() => {
    if (!open) return;
    listRef.current?.children[active]?.scrollIntoView({ block: 'nearest' });
  }, [open, active]);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (!wrapRef.current?.contains(e.target)) closeList(); };
    document.addEventListener('pointerdown', onDown);
    return () => document.removeEventListener('pointerdown', onDown);
  }, [open]);

  const onKeyDown = (e) => {
    if (disabled) return;
    const last = options.length - 1;
    if (!open) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) {
        e.preventDefault();
        openList();
      }
      return;
    }
    switch (e.key) {
      case 'ArrowDown': e.preventDefault(); setActive((i) => Math.min(i + 1, last)); break;
      case 'ArrowUp': e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); break;
      case 'Home': e.preventDefault(); setActive(0); break;
      case 'End': e.preventDefault(); setActive(last); break;
      case 'Enter':
      case ' ': e.preventDefault(); pick(active); break;
      // stopPropagation: inside the inquiry dialog, Escape should close just
      // this list, not the whole dialog behind it.
      case 'Escape': e.preventDefault(); e.stopPropagation(); closeList(); break;
      case 'Tab': closeList(); break;
      default:
        if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
          const now = Date.now();
          typed.current.text = (now - typed.current.at > 600 ? '' : typed.current.text) + e.key.toLowerCase();
          typed.current.at = now;
          const from = typed.current.text.length === 1 ? active + 1 : active;
          const order = [...options.keys()];
          const hit = [...order.slice(from), ...order.slice(0, from)]
            .find((i) => String(options[i].label).toLowerCase().startsWith(typed.current.text));
          if (hit !== undefined) setActive(hit);
        }
    }
  };

  return (
    <div className={`select${open ? ' is-open' : ''}${up ? ' is-up' : ''}`} ref={wrapRef}>
      <button
        type="button"
        id={buttonId}
        ref={setButton}
        className="input select-btn"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={open ? `${buttonId}-opt-${active}` : undefined}
        disabled={disabled}
        onClick={() => (open ? closeList() : openList())}
        onKeyDown={onKeyDown}
        onBlur={(e) => { if (!wrapRef.current?.contains(e.relatedTarget)) closeList(); }}
      >
        <span className={selected ? 'select-value' : 'select-value is-placeholder'}>
          {selected ? selected.label : placeholder}
        </span>
        <CaretDownIcon className="select-caret" size={16} weight="bold" aria-hidden="true" />
      </button>

      {required && (
        <input
          className="select-proxy"
          tabIndex={-1}
          aria-hidden="true"
          required
          value={selected ? String(selected.value) : ''}
          onChange={() => {}}
          onInvalid={() => innerButtonRef.current?.focus({ preventScroll: true })}
        />
      )}

      {open && (
        <div className="select-panel" id={listId} role="listbox" ref={listRef}>
          {options.map((o, i) => (
            <div
              key={String(o.value)}
              id={`${buttonId}-opt-${i}`}
              role="option"
              aria-selected={i === selectedIndex}
              className={`select-option${i === active ? ' is-active' : ''}`}
              onPointerDown={(e) => e.preventDefault()}
              onPointerEnter={() => setActive(i)}
              onClick={() => pick(i)}
            >
              <span>{o.label}</span>
              {i === selectedIndex && <CheckIcon size={16} weight="bold" aria-hidden="true" />}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
