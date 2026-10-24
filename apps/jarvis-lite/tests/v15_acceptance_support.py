"""Synthetic responses only; actual AI adapter/HTTP validation remains enabled."""
from jarvis_lite import engine
from test_autonomous_research_v15 import TEXT


def content_for(role, contract, prompt):
    if contract == 'ceo_plan_research':
        content = {'tasks': [{'role': r, 'objective': 'Bearbeta godkänt internt underlag för ' + r} for r in engine.SPECIALISTS],
                   'information_needs': ['Vilka belägg finns för småföretags AI-användning?']}
    elif role == 'lead_research':
        content = {'summary': TEXT, 'candidates': [], 'findings': [], 'uncertainties': ['Syntetiskt underlag kräver mänsklig granskning']}
    elif role == 'sales':
        claim = prompt['verified_knowledge']['claims'][0]
        content = {'summary': 'Intern hypotes, inget efterfrågebevis', 'opportunities': [{
            'title': 'Hypotes om intern AI-assistans', 'target_group': 'Målgrupp att validera', 'problem': claim['text'],
            'solution': 'Internt granskade utkast', 'price_estimate_minor': 500000, 'cost_estimate_minor': 100000,
            'competition': 'Ej verifierad', 'risks': ['Efterfrågan måste valideras'],
            'source_ids': [c['source_id'] for c in claim['citations']], 'next_steps': ['Validera hypotes internt'],
            'assumptions': ['Priser och kostnader är obekräftade'], 'classification': 'HYPOTHESIS',
            'claim_ids': [claim['id']], 'factual_claims': [{'claim_id': claim['id'], 'text': claim['text']}]}]}
    elif role == 'delivery':
        claim = prompt['verified_knowledge']['claims'][0]
        content = {'title': 'Intern affärsplan med osäkerheter', 'sections': [{'key': key, 'heading': key,
            'content': 'Hypotes för intern validering. Verkliga kundbehov, kostnader och efterfrågan har inte verifierats.',
            'source_ids': [prompt['source_evidence'][0]['id']]} for key in prompt['required_section_keys']],
            'next_actions': ['Granska affärsantaganden internt'], 'limitations': ['Syntetiska belägg och okända arbetskostnader kräver validering'],
            'company_ids': [], 'classification': 'HYPOTHESIS', 'factual_claims': [{'claim_id': claim['id'], 'text': claim['text']}]}
    elif role == 'compliance':
        content = {'verdict': 'pass', 'findings': [], 'summary': 'Intern kvalitet kontrollerad, inga externa behörigheter ges',
                   'source_ids': prompt.get('source_ids', prompt.get('facts', {}).get('source_ids', []))}
    else:
        content = 'Intern sammanställning: priser är hypoteser, verkliga kostnader saknas och inga intäkter eller kundkontakter är verifierade.'
    return content
