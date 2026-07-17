import pytest
from app.services.imt import (
    ImtFetchError,
    parse_competency_ues,
    parse_pass_export,
    parse_pass_profile,
    validate_imt_url,
)


def test_parser_extracts_only_grade_rows_and_resits() -> None:
    html = """
    <table>
      <tr><td>Evaluation SIT130</td></tr>
      <tr><td>Libellé</td><td>Règle</td><td>Coefficient</td><td>Notes</td></tr>
      <tr><td>Projet</td><td>Classique /20</td><td>2</td><td>15,5</td></tr>
      <tr><td>RAT1</td><td>Classique /20</td><td>1</td><td>12</td></tr>
      <tr><td>Information personnelle</td><td>texte</td><td>-</td><td>-</td></tr>
    </table>
    """
    entries = parse_pass_export(html)
    assert len(entries) == 2
    assert entries[0].ue_code == "SIT130"
    assert entries[0].score == 15.5
    assert entries[1].is_resit is True


@pytest.mark.parametrize(
    "url",
    [
        "http://pass.imt-atlantique.fr/OpDotNet/",
        "https://pass.imt-atlantique.fr.evil.example/OpDotNet/",
        "https://pass.imt-atlantique.fr@evil.example/OpDotNet/",
        "https://pass.imt-atlantique.fr:444/OpDotNet/",
    ],
)
def test_imt_url_policy_rejects_lookalike_destinations(url: str) -> None:
    with pytest.raises(ImtFetchError):
        validate_imt_url(url)


def test_imt_url_policy_accepts_exact_https_origin() -> None:
    url = "https://pass.imt-atlantique.fr/OpDotNet/report?id=1"
    assert validate_imt_url(url) == url


def test_imt_url_policy_accepts_exact_competencies_origin() -> None:
    url = "https://hub.imt-atlantique.fr/comp2/etudiant/40419/ue"
    assert validate_imt_url(url) == url


def test_competencies_parser_imports_official_titles_and_attempted_credits() -> None:
    content = """
    <table>
      <thead><tr><th>UE</th><th>Semestre</th><th>Code</th><th>Grade</th><th>Obtenus</th><th>Tentés</th></tr></thead>
      <tbody>
        <tr>
          <td>Outils physiques pour l'ingénieur S5</td><td>Semestre 1</td>
          <td>FIP-SIT140-BR-2025</td><td>E</td><td>3.00</td><td>3.00</td>
        </tr>
        <tr>
          <td>Projet en cours</td><td>Semestre 2</td>
          <td>FIP-PRJ120-BR-2026</td><td>FX</td><td>0.00</td><td>5.00</td>
        </tr>
      </tbody>
    </table>
    """

    entries = parse_competency_ues(content)

    assert entries[0].ue_code == "SIT140"
    assert entries[0].title == "Outils physiques pour l'ingénieur S5"
    assert entries[0].credits_ects == 3
    assert entries[1].ue_code == "PRJ120"
    assert entries[1].credits_ects == 5


def test_competencies_parser_rejects_conflicting_duplicate_codes() -> None:
    content = """
    <table>
      <tr>
        <td>UE initiale</td><td>Semestre 1</td><td>FIP-SIT140-BR-2025</td>
        <td>B</td><td>3</td><td>3</td>
      </tr>
      <tr>
        <td>UE modifiée</td><td>Semestre 1</td><td>FIP-SIT140-BR-2025</td>
        <td>B</td><td>4</td><td>4</td>
      </tr>
    </table>
    """

    with pytest.raises(ImtFetchError, match="contradictoires"):
        parse_competency_ues(content)


@pytest.mark.parametrize("score, coefficient", [("NaN", "1"), ("12", "Infinity"), ("12", "101")])
def test_parser_rejects_non_finite_or_oversized_values(score: str, coefficient: str) -> None:
    content = f"""
    <table>
      <tr><td>Evaluation SIT130</td></tr>
      <tr><td>Projet</td><td>Classique /20</td><td>{coefficient}</td><td>{score}</td></tr>
    </table>
    """
    with pytest.raises(ImtFetchError):
        parse_pass_export(content)


def test_parser_deduplicates_storage_identity_and_keeps_latest_score() -> None:
    content = """
    <table>
      <tr><td>Evaluation SIT130</td></tr>
      <tr><td>Projet</td><td>Classique /20</td><td>2</td><td>12</td></tr>
      <tr><td>Projet</td><td>Classique /20</td><td>2</td><td>16</td></tr>
    </table>
    """

    entries = parse_pass_export(content)

    assert len(entries) == 1
    assert entries[0].score == 16


def test_parser_rejects_row_overflow_before_dom_build(monkeypatch) -> None:
    monkeypatch.setattr("app.services.imt.MAX_PASS_ROWS", 1)
    content = "<table><tr><td>one</td></tr><tr><td>two</td></tr></table>"

    with pytest.raises(ImtFetchError, match="trop de lignes"):
        parse_pass_export(content)


def test_profile_parser_extracts_only_current_campus() -> None:
    content = """
    <div class="form_field">
      <span class="form_fieldLabel" id="Lbl_IMTA_CAMPUS_COUR">Campus courant :</span>
      <div class="form_fieldValue"> Rennes </div>
    </div>
    <div>Adresse personnelle : information non pertinente</div>
    """

    profile = parse_pass_profile(content)

    assert profile.campus == "Rennes"


def test_profile_parser_keeps_missing_campus_explicit() -> None:
    assert parse_pass_profile("<div>Profil sans campus</div>").campus is None


def test_profile_parser_extracts_program_and_expected_exit_year() -> None:
    content = """
    <div class="form_field">
      <span class="form_fieldLabel" id="Lbl_IMTA_CURSUS_PRIMO_INSCRIPTION">
        Cursus primo inscription :
      </span>
      <div class="form_fieldValue"> FIP </div>
    </div>
    <div class="form_field">
      <span class="form_fieldLabel" id="Lbl_IMTA_DATE_SORTIE_PREVI">
        Date prévi. de sortie :
      </span>
      <div class="form_fieldValue"> 31/12/2028 </div>
    </div>
    """

    profile = parse_pass_profile(content)

    assert profile.program == "FIP"
    assert profile.promotion_year == 2028


def test_profile_parser_extracts_exact_official_first_and_last_name() -> None:
    content = """
    <div class="form_field">
      <span class="form_fieldLabel">Nom de naissance :</span>
      <div class="form_fieldValue">SHOULD NOT MATCH</div>
    </div>
    <div class="form_field">
      <span class="form_fieldLabel" id="Lbl_NOM">Nom :</span>
      <div class="form_fieldValue"> MARTIN </div>
    </div>
    <div class="form_field">
      <span class="form_fieldLabel" id="Lbl_PRENOM">Prénom :</span>
      <div class="form_fieldValue"> Camille </div>
    </div>
    """

    profile = parse_pass_profile(content)

    assert profile.first_name == "Camille"
    assert profile.last_name == "MARTIN"
