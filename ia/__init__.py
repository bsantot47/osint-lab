"""IA locale, strictement délimitée : elle propose, l'enquêteur décide.

Aucun module de ce paquet ne fait d'appel réseau : spaCy charge un modèle
installé localement (spacy.load ne télécharge jamais rien). L'IA ne marque
jamais un fait confirmé, ne complète aucune information, ne trie pas les
pistes par importance.
"""
