// "How our scents unfold" copy (the ScentJourney section), kept in one
// place like about.js.
//
// TODO(content): the `notes` below are PLACEHOLDER examples of each layer,
// not the real formulas. Replace them with the actual top, heart and base
// notes of Uriel H1 and Raphael A3 before sharing the portal link: the
// section presents them as those two scents' notes, and partners will
// repeat them to their customers. One to three notes per scent per layer
// keeps the layout tidy; the first one of each also floats over that
// bottle in the scene.

/** The two bottles in the scene, left to right (see PerfumeScene). */
export const SCENTS = [
  { key: 'uriel', name: 'Uriel H1' },
  { key: 'raphael', name: 'Raphael A3' },
];

export const LAYERS = [
  {
    name: 'Top notes',
    when: 'First 15 minutes',
    title: 'The first impression.',
    body: 'Bright, quick notes greet you the moment it touches skin, then lift away to make room for what comes next.',
    notes: { uriel: ['Bergamot', 'Pink pepper'], raphael: ['Lemon', 'Mint'] },
  },
  {
    name: 'Heart notes',
    when: '2 to 4 hours',
    title: 'The character.',
    body: 'As the opening fades, the heart rises. This is the personality of the scent, the part people remember.',
    notes: { uriel: ['Rose', 'Jasmine'], raphael: ['Lavender', 'Sea salt'] },
  },
  {
    name: 'Base notes',
    when: '6 hours and beyond',
    title: 'What stays.',
    body: 'Deep, slow notes settle in last and linger on skin and fabric long after the rest has gone.',
    notes: { uriel: ['Amber', 'Musk'], raphael: ['Sandalwood', 'Cedar'] },
  },
];
