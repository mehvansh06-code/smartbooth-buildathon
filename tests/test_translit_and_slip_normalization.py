# -*- coding: utf-8 -*-
"""Unit tests verifying transliteration, Rajasthani surname normalization, and slip sanitization."""
import unittest
from electionapp.translit.rules import transliterate_name
from electionapp.translit.normalizer import normalize_voter_names, clean_devnagari_name, clean_english_name
from electionapp.blueprints.slip import format_bilingual


class TestTranslitAndSlipNormalization(unittest.TestCase):
    def test_rathore_transliteration(self):
        """Verify राठौड़ and its variants transliterate to Rathore, never Rathaur."""
        self.assertEqual(transliterate_name('राठौड़'), 'Rathore')
        self.assertEqual(transliterate_name('राठौड़'), 'Rathore')
        self.assertEqual(transliterate_name('राठोड'), 'Rathore')
        self.assertEqual(transliterate_name('राठौड'), 'Rathore')
        self.assertEqual(transliterate_name('राठोड़'), 'Rathore')
        self.assertEqual(transliterate_name('राठोर'), 'Rathore')
        self.assertEqual(transliterate_name('सुनील सिंह राठौड़'), 'Sunil Singh Rathore')
        self.assertEqual(transliterate_name('महेन्द्र सिंह राठौड़'), 'Mahendra Singh Rathore')

    def test_rajasthani_surnames(self):
        """Verify common Rajasthani surnames transliterate according to regional standard."""
        self.assertEqual(transliterate_name('चौधरी'), 'Chaudhary')
        self.assertEqual(transliterate_name('चोधरी'), 'Chaudhary')
        self.assertEqual(transliterate_name('चौधारी'), 'Chaudhary')
        self.assertEqual(transliterate_name('शेखावत'), 'Shekhawat')
        self.assertEqual(transliterate_name('कुमावत'), 'Kumawat')
        self.assertEqual(transliterate_name('गहलोत'), 'Gehlot')
        self.assertEqual(transliterate_name('जांगिड़'), 'Jangid')
        self.assertEqual(transliterate_name('सिसोदिया'), 'Sisodia')
        self.assertEqual(transliterate_name('पूनिया'), 'Poonia')
        self.assertEqual(transliterate_name('बिश्नोई'), 'Bishnoi')
        self.assertEqual(transliterate_name('विश्नोई'), 'Bishnoi')

    def test_gyani_ram_and_gyan_words(self):
        """Verify ज्ञानी and ज्ञान words transliterate to Gyani/Gyan, never Jnani or Ni Ram."""
        self.assertEqual(transliterate_name('ज्ञानी राम'), 'Gyani Ram')
        self.assertEqual(transliterate_name('ज्ञानीराम'), 'Gyaniram')
        self.assertEqual(transliterate_name('ज्ञानचंद'), 'Gyanchand')
        self.assertEqual(transliterate_name('ज्ञान प्रकाश'), 'Gyan Prakash')

    def test_normalizer_dropped_ligatures_restoration(self):
        """Verify normalizer heals dropped OCR ligatures."""
        res1 = normalize_voter_names('सुनील कुमार सैनी', 'Sunil Kumar Saini', 'ानी राम', 'Ni Ram')
        self.assertEqual(res1[0], 'सुनील कुमार सैनी')
        self.assertEqual(res1[1], 'Sunil Kumar Saini')
        self.assertEqual(res1[3], 'ज्ञानी राम')
        self.assertEqual(res1[4], 'Gyani Ram')

        res2 = normalize_voter_names('मदन कोर', 'Madan Kor', 'ूल सिंह', 'La Singh')
        self.assertEqual(res2[3], 'मूल सिंह')
        self.assertEqual(res2[4], 'Mool Singh')

        res3 = normalize_voter_names('मनोज मीणा', 'Manoj Meena', 'ारका प्रसाद मीणा', 'Raka Prasad Meena')
        self.assertEqual(res3[3], 'द्वारका प्रसाद मीणा')
        self.assertEqual(res3[4], 'Dwarka Prasad Meena')

        res4 = normalize_voter_names('ेम देवी', 'Ma Devi', 'राकेश सिंह', 'Rakesh Singh')
        self.assertEqual(res4[0], 'प्रेम देवी')
        self.assertEqual(res4[1], 'Prem Devi')

    def test_stray_diacritics_stripped(self):
        """Verify stray leading diacritics are cleanly stripped from names."""
        self.assertEqual(clean_devnagari_name('ुफूलचन्द'), 'फूलचन्द')
        self.assertEqual(clean_devnagari_name('ुसुमन'), 'सुमन')
        self.assertEqual(clean_devnagari_name('ंगंगादेवी'), 'गंगा देवी')
        self.assertEqual(clean_devnagari_name('ेसचिन कुमार सैनी'), 'सचिन कुमार सैनी')
        self.assertEqual(clean_devnagari_name('महेश ंसिह'), 'महेश सिंह')
        self.assertEqual(clean_devnagari_name('सविता ंकंवर'), 'सविता कंवर')
        self.assertEqual(clean_devnagari_name('सविता ेदवी'), 'सविता देवी')

    def test_gaurav_and_v_w_phonetics(self):
        """Verify गौरव, सौरव, भैरव, ध्रुव, अरविंद transliterate to -v, never -w."""
        self.assertEqual(transliterate_name('गौरव'), 'Gaurav')
        self.assertEqual(transliterate_name('गौरव सिंह'), 'Gaurav Singh')
        self.assertEqual(transliterate_name('सौरव'), 'Saurav')
        self.assertEqual(transliterate_name('भैरव'), 'Bhairav')
        self.assertEqual(transliterate_name('ध्रुव'), 'Dhruv')
        self.assertEqual(transliterate_name('आरव'), 'Aarav')
        self.assertEqual(transliterate_name('नीरव'), 'Neerav')
        self.assertEqual(transliterate_name('अरविंद'), 'Arvind')
        self.assertEqual(transliterate_name('अरविन्द'), 'Arvind')
        self.assertEqual(transliterate_name('वासुदेव'), 'Vasudev')
        self.assertEqual(transliterate_name('वाजपेयी'), 'Vajpayee')
        self.assertEqual(transliterate_name('कंवर'), 'Kanwar')
        self.assertEqual(transliterate_name('बैरवा'), 'Bairwa')
        self.assertEqual(transliterate_name('मेघवाल'), 'Meghwal')
        self.assertEqual(transliterate_name('शेखावत'), 'Shekhawat')

    def test_chand_anand_terminal_nasals(self):
        """Verify चन्द/चंद -> Chand, आनन्द/आनंद -> Anand, etc."""
        self.assertEqual(transliterate_name('मुकेश चन्द'), 'Mukesh Chand')
        self.assertEqual(transliterate_name('मुकेश चंद'), 'Mukesh Chand')
        self.assertEqual(transliterate_name('रामचन्द'), 'Ramchand')
        self.assertEqual(transliterate_name('हरचन्द'), 'Harchand')
        self.assertEqual(transliterate_name('आनन्द'), 'Anand')
        self.assertEqual(transliterate_name('आनंद'), 'Anand')
        self.assertEqual(transliterate_name('हेमन्त'), 'Hemant')
        self.assertEqual(transliterate_name('वसन्त'), 'Vasant')
        self.assertEqual(transliterate_name('जयन्त'), 'Jayant')
        self.assertEqual(transliterate_name('बलवन्त'), 'Balvant')
        self.assertEqual(transliterate_name('गोविन्द'), 'Govind')

    def test_rajeshwari_shwar_clusters(self):
        """Verify -श्वरी and -श्वर preserve conjunct cluster vowels and use shw."""
        self.assertEqual(transliterate_name('राजेश्वरी'), 'Rajeshwari')
        self.assertEqual(transliterate_name('महेश्वरी'), 'Maheshwari')
        self.assertEqual(transliterate_name('रामेश्वर'), 'Rameshwar')
        self.assertEqual(transliterate_name('राजेश्वर'), 'Rajeshwar')
        self.assertEqual(transliterate_name('ईश्वर'), 'Ishwar')

    def test_slip_format_bilingual(self):
        """Verify voter slip format_bilingual heals legacy corrupted entries at runtime."""
        slip_voter = format_bilingual('Ni Ram', 'ानी राम')
        self.assertEqual(slip_voter, 'Gyani Ram / ज्ञानी राम')

        slip_rathore = format_bilingual('Sunil Singh Rathaur', 'सुनील सिंह राठौड़')
        self.assertEqual(slip_rathore, 'Sunil Singh Rathore / सुनील सिंह राठौड़')

        slip_gaurav = format_bilingual('Gauraw', 'गौरव')
        self.assertEqual(slip_gaurav, 'Gaurav / गौरव')


if __name__ == '__main__':
    unittest.main()
