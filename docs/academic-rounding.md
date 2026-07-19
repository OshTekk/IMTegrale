# Politique d'arrondi académique

Les données officielles restent affichées selon leur valeur source, mais leur représentation persistée doit être déterministe sur SQLite, PostgreSQL, Python et le frontend.

## Stockage

- note sur 20 : `Numeric(5,2)` ;
- coefficient de note : `Numeric(7,3)` ;
- crédits ECTS obtenus ou alloués : `Numeric(6,2)` ;
- grades et points GPA : barème explicite, sans conversion flottante persistée.

Chaque valeur est convertie depuis sa forme textuelle vers `Decimal`, contrôlée comme finie puis quantifiée avec `ROUND_HALF_UP` à l'entrée de la base. Les calculs intermédiaires d'une UE utilisent `Decimal`. La conversion en nombre JSON intervient seulement à la frontière de réponse lorsque le contrat public attend un `number`.

## Migration

La révision `0023` arrondit les anciennes colonnes `Float` avec la même précision avant conversion PostgreSQL vers `Numeric`. Son downgrade restaure le type flottant mais ne peut pas recréer les décimales qui auraient existé au-delà de la précision officielle : une sauvegarde pré-release reste la seule restauration bit à bit.

Les simulations utilisaient déjà `Decimal/Numeric`; cette politique confirme leur règle existante et l'étend aux notes PASS et métadonnées COMPETENCES.
