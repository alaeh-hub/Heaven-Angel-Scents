// "About the brand" copy, kept in one place so the real text can be
// dropped in without touching any layout code.
//
// TODO(content): the copy below is a DRAFT written from what the site
// already says (small runs, 85 ml, partner pricing); it states no years,
// names or figures. Review it and replace it with the real story (who's behind the brand, how long you've been
// making scents, what makes the formulations different) before sharing
// the portal link. Keep `lede` to about 2 sentences and each point body
// to about 25 words so the layout keeps its rhythm.
//
// `photos`: `main` shows large and landscape (4:3), `detail` is a smaller
// portrait (4:5) that overlaps its corner. Use WebP or JPG, about 1600px on
// the long side, and write `alt` as what the photo actually shows.

export const ABOUT = {
  title: 'About Heaven & Angel Scents.',
  photos: {
    // Stock photos from Unsplash (free under the Unsplash License, no
    // attribution required; credited here anyway). Swap in your own shots
    // by replacing the two files in static/img/about/.
    //   bottles.webp Lera Ginzburg, https://unsplash.com/photos/N8WxMVijPKw
    //   dropper.webp Fulvio Ciccolo, https://unsplash.com/photos/dtLGvOl0hEs
    main: { src: '/static/img/about/bottles.webp', alt: 'Five perfume bottles with blank labels standing in raking light and shadow' },
    detail: { src: '/static/img/about/dropper.webp', alt: 'A perfumer holding a glass dropper over a bottle, about to add golden liquid' },
  },
  lede:
    'We craft eau de parfum for those who want a scent that feels unmistakably their own, and we supply the distributors and resellers who place it in their hands.',
  points: [
    {
      key: 'people',
      title: "Who's behind it",
      body: 'A small in-house team blends, bottles and cares for our partners together, so whoever answers your message knows the very scent you are asking about.',
    },
    {
      key: 'years',
      title: 'How long we have been at it',
      body: 'We have grown the range one scent at a time, keeping what customers return for and refining the rest, so partners begin with that experience from their first order.',
    },
    {
      key: 'formula',
      title: 'What makes our formulations different',
      body: 'Every fragrance is made in small runs and bottled at 85 ml, its top, heart and base notes balanced to open bright and settle warm on the skin.',
    },
  ],
};
