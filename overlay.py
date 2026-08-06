"""
Hand-curated overlay: information the FPL API does not encode.

Everything here traces to a specific piece of research (pre-season Scout Notes,
transfer reporting, manager appointments) gathered on 6 Aug 2026. Keys are FPL
element ids. `rate_mult` scales the projected points rate; `mins` overrides the
projected minutes per gameweek; `note` is surfaced in the app.

Deliberately conservative: multipliers stay in roughly 0.75-1.25 so the model is
still driven by data, not by vibes.
"""

# Clubs with a new manager for 2026/27 -- their tactical output is less predictable,
# so the model trims projections slightly and the app flags them.
NEW_MANAGER = {
    3:  'Marco Rose (replaced Iraola)',
    6:  'Xabi Alonso (replaced Rosenior)',
    8:  'Pierre Sage (replaced Glasner)',
    10: 'Alvaro Arbeloa',
    12: 'Gary O\'Neil (replaced McKenna)',
    14: 'Andoni Iraola (replaced Slot)',
    15: 'Enzo Maresca (replaced Guardiola)',
    17: 'Matthias Jaissle (replaced Howe)',
    18: 'Oliver Glasner',
    19: 'Roberto De Zerbi',
}

# Pre-season form notes, surfaced in the app.
PRESEASON_FORM = {
    70:  '3 goals v Genoa incl. 2 pens - now looks first-choice from the spot',
    69:  '2 goals in the 10-1 win over Genoa',
    427: '2 goals v Atletico incl. a penalty, played centrally',
    397: 'Assisted in the Inter friendly; Maresca is using him wide',
    398: 'Captained City v Inter; named one of Maresca\'s "three central pillars"',
    491: '4 goals in 4 pre-season games',
    157: 'Scored v Spurs; first goal of pre-season',
    158: '3 goals + 2 assists across pre-season',
    165: '4 goals across pre-season',
    41:  '2 goals + 3 assists in pre-season',
    346: 'Brace in pre-season; 14 PL goals last season at £6.0m',
    246: 'Scored from the right; carrying end-of-season form into pre-season',
    557: 'Goal + assist on his Arsenal debut',
    20:  'Goal + assist in pre-season',
    552: 'Excellent pre-season, assist v Wrexham',
    271: 'Converted a penalty v Farense - Fulham\'s pen situation is open',
    124: 'On penalties for Brighton in pre-season',
    455: 'Scored v Chelsea',
    315: 'Created the winner v Le Havre',
    562: 'Impressed with pressing and directness',
    47:  'Scored on his return after 7 months out',
    368: 'Being used deeper by Iraola - 1 goal 2 assists in pre-season',
}

OVERLAY = {
    # ---- role / opportunity changes -------------------------------------
    70: {'rate_mult': 1.18, 'mins': 68,
         'note': 'Bournemouth\'s pen taker with Kroupi injured; hat-trick incl. 2 pens v Genoa'},
    427: {'rate_mult': 1.10,
          'note': 'Playing centrally under Carrick and second on pens behind Bruno'},
    397: {'rate_mult': 0.95,
          'note': 'Wide role under Maresca may cost some of last season\'s central output'},
    398: {'rate_mult': 1.08, 'mins': 68,
          'note': 'One of Maresca\'s "three central pillars"; on corners and FKs'},
    368: {'rate_mult': 0.92,
          'note': 'Iraola using him deeper; still on pens/corners/FKs but further from goal'},

    # ---- Arsenal defence: Saliba out, Timber returning -------------------
    11: {'rate_mult': 1.05, 'mins': 76,
         'note': 'Should start with Saliba out long-term'},
    9:  {'mins': 58,
         'note': 'New signing; rotation risk with Calafiori and Mosquera'},
    8:  {'mins': 68},

    # ---- Spurs goalkeeper: De Zerbi has settled on Kinsky ----------------
    # Dubravka's 3,150 minutes last season were at Burnley; at Spurs he is the
    # backup, so he is a pure £4.0m bench enabler rather than a starter.
    496: {'mins': 88, 'note': 'De Zerbi\'s confirmed No.1 after a new long-term contract'},
    497: {'mins': 4,  'note': 'Signed as Kinsky\'s BACKUP - a £4.0m non-playing bench enabler only'},
    494: {'mins': 4,  'note': 'Being moved on; Spurs are working to sell him'},

    # ---- squad-depth reality checks --------------------------------------
    # Welbeck's minutes came as Brighton's regular starter; at Chelsea he is
    # behind Joao Pedro, Delap and Emegha.
    136: {'mins': 32, 'note': 'Surprise Chelsea signing at 35; squad role behind Joao Pedro'},

    # ---- promoted clubs: keep expectations grounded ----------------------
    175: {'rate_mult': 0.90, 'note': 'Promoted-side defender; cheap DefCon punt'},
    259: {'rate_mult': 0.92, 'note': '£4.0m Ipswich defender under O\'Neil\'s organised setup'},

    # ---- injuries the API flags loosely but which are effectively season-shaping
    # (the status flags already handle the maths; these add context for the app)
    379: {'note': 'Liverpool\'s #1 penalty taker under Iraola'},
    12:  {'note': 'Arsenal\'s #1 penalty taker'},
    154: {'note': 'Chelsea\'s #1 penalty taker under Alonso'},
    411: {'note': 'City\'s #1 pen taker; 37 goals in his first six games across seasons'},
    426: {'note': 'United\'s #1 pen taker, on corners and FKs; 24 assists last season'},

    # ---- players who left / are unavailable ------------------------------
    # (status 'u' already zeroes these; listed for transparency in the app)
}
